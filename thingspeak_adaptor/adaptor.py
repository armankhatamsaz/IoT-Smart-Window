import os
import sys
import time
import json
import random
import string
import threading
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import cherrypy
import requests
import paho.mqtt.client as mqtt

THINGSPEAK_URL = "https://api.thingspeak.com"
MAX_RESULTS = 8000  # ThingSpeak returns at most this many entries per request


def cors():
    cherrypy.response.headers["Access-Control-Allow-Origin"] = "*"
    cherrypy.response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    cherrypy.response.headers["Access-Control-Allow-Headers"] = "Content-Type"

cherrypy.tools.cors = cherrypy.Tool('before_handler', cors)


def senml_value(payload):
    """Extract the first measurement value from a SenML document (RFC 8428)."""
    records = payload.get("e")
    if isinstance(records, list) and records:
        return records[0].get("v")
    return None


class AdaptorWebServer(object):
    """REST interface to the stored data. Nobody else (the dashboard included)
    talks to ThingSpeak or needs to know its field numbers."""
    exposed = True

    def __init__(self, adaptor):
        self.adaptor = adaptor

    @cherrypy.tools.json_out()
    def OPTIONS(self, *uri, **params):
        return {"status": "OK"}

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        if len(uri) > 0 and uri[0] == "history":
            try:
                results = int(params.get("results", self.adaptor.history_results))
            except ValueError:
                raise cherrypy.HTTPError(400, "results must be a whole number")
            results = max(1, min(results, MAX_RESULTS))
            try:
                return {"samples": self.adaptor.read_history(results)}
            except Exception as e:
                raise cherrypy.HTTPError(502, f"Could not read from ThingSpeak: {e}")
        return {"message": "ThingSpeak adaptor. GET /history?results=N returns the stored measurements."}


class ThingSpeakAdaptor:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.client = None

        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        self.service_id = f"ts_adaptor_{random_suffix}"

        # MQTT for ingesting data, REST for serving it back. The REST address
        # is the one other containers use to reach us.
        self.service_info = {
            "serviceID": self.service_id,
            "serviceName": self.service_name,
            "availableServices": ["MQTT", "REST"],
            "servicesDetails": [
                {
                    "serviceType": "MQTT",
                    "topic": ["home/+/sensor/+", "home/+/actuator/window"]
                },
                {
                    "serviceType": "REST",
                    "serviceIP": self.service_ip,
                    "endpoints": ["/history"]
                }
            ]
        }

        # One outgoing buffer per channel.
        self.buffers = {channel["name"]: {} for channel in self.channels}

    def load_settings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.service_name = settings.get("service_name", "ThingSpeak Adaptor")
                self.service_ip = settings.get("service_ip", "adaptor:8081")
                self.history_results = settings.get("history_results", 100)
                self.channels = settings.get("channels", [])
        except FileNotFoundError:
            print("Adaptor settings.json not found.")
            sys.exit(1)

        # API keys are secrets, so they come from .env instead of this file.
        for number, channel in enumerate(self.channels, start=1):
            channel["api_key"] = os.environ.get(f"THINGSPEAK_CH{number}_WRITE_KEY") or channel.get("api_key", "")
            channel["read_key"] = os.environ.get(f"THINGSPEAK_CH{number}_READ_KEY") or channel.get("read_key", "")
            if not channel["api_key"]:
                print(f"[{channel['name']}] no write key set, this channel is not logged.")

    def discover_and_register(self):
        print(f"Contacting registry at {self.registry_url}...")
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/broker", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["IP"]
                    self.port = config["port"]
                    self.transport = config["transport"]
                    print("Registry config received.")
                    break
            except Exception:
                time.sleep(3)

        try:
            resp = requests.post(f"{self.registry_url}/servicesList", json=self.service_info)
            if resp.status_code == 200:
                print("Adaptor registered in catalog (MQTT + REST).")
        except Exception as e:
            print(f"Registration failed: {e}")

    def heartbeat_worker(self):
        while True:
            time.sleep(50)
            try:
                requests.put(
                    f"{self.registry_url}/servicesList",
                    json={"id": self.service_id},
                    timeout=5
                )
            except Exception:
                pass

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Adaptor connected to MQTT broker.")
            for channel in self.channels:
                for topic in channel["mapping"].keys():
                    client.subscribe(topic)
        else:
            print(f"MQTT connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            if not isinstance(payload, dict):
                return
            # Sensors publish SenML; the window actuator publishes a plain position.
            value = senml_value(payload)
            if value is None:
                value = payload.get("position")
            if value is None:
                return
            for channel in self.channels:
                if msg.topic in channel["mapping"]:
                    self.buffers[channel["name"]][channel["mapping"][msg.topic]] = value
        except json.JSONDecodeError:
            pass

    def thingspeak_worker(self):
        url = f"{THINGSPEAK_URL}/update"
        while True:
            # ThingSpeak throttles free channels to one update per 15s.
            time.sleep(20)

            for channel in self.channels:
                name = channel["name"]
                if not channel["api_key"] or not self.buffers[name]:
                    continue

                # Swap in an empty buffer first, so values that arrive during
                # the upload are not lost when we clear.
                sending = self.buffers[name]
                self.buffers[name] = {}

                data = {"api_key": channel["api_key"]}
                data.update(sending)
                uploaded = False
                try:
                    response = requests.post(url, data=data, timeout=10)
                    # ThingSpeak answers 200 with a body of "0" when it rejects an update.
                    uploaded = response.status_code == 200 and response.text.strip() != "0"
                except Exception:
                    pass

                if uploaded:
                    print(f"[{channel['name']}] uploaded.")
                else:
                    print(f"[{channel['name']}] not accepted by ThingSpeak, retrying next round.")
                    for field, value in sending.items():
                        self.buffers[name].setdefault(field, value)

    def read_history(self, results):
        """Read every channel back from ThingSpeak and merge them into one
        time-ordered list of snapshots: {"t": time, "zones": {zone: {name: value}}}.

        The first channel sets the time grid. Values from the other channels are
        carried forward onto it, which also fills the gaps of readings that are
        only stored when they change (the window position)."""
        rows = []
        for index, channel in enumerate(self.channels):
            if not channel.get("channel_id") or not channel.get("read_key"):
                continue  # write-only channel, nothing to read back
            try:
                resp = requests.get(
                    f"{THINGSPEAK_URL}/channels/{channel['channel_id']}/feeds.json",
                    params={"api_key": channel["read_key"], "results": results},
                    timeout=15
                )
                resp.raise_for_status()
                feeds = resp.json().get("feeds", [])
            except Exception as e:
                if index == 0:
                    raise  # without the main channel there is no history at all
                print(f"[{channel['name']}] could not be read back: {e}")
                continue
            for entry in feeds:
                rows.append((entry["created_at"], index, channel, entry))

        rows.sort(key=lambda row: (row[0], row[1]))
        state = {}
        samples = []
        for created_at, index, channel, entry in rows:
            for topic, field in channel["mapping"].items():
                raw = entry.get(field)
                if raw in (None, ""):
                    continue
                parts = topic.split("/")  # home/<zone>/<sensor|actuator>/<name>
                state.setdefault(parts[1], {})[parts[3]] = float(raw)
            if index == 0:
                samples.append({"t": created_at, "zones": {z: dict(v) for z, v in state.items()}})
        return samples[-results:]

    def start(self):
        self.discover_and_register()
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()

        self.client = mqtt.Client(client_id=f"MQTT_{self.service_id}", transport=self.transport)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        print("Connecting to broker...")
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()

        threading.Thread(target=self.thingspeak_worker, daemon=True).start()

        port = int(self.service_ip.split(":")[1])
        print(f"Starting adaptor REST server on port {port}...")
        conf = {
            '/': {
                'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
                'tools.cors.on': True
            }
        }
        cherrypy.tree.mount(AdaptorWebServer(self), '/', conf)
        cherrypy.config.update({'server.socket_host': '0.0.0.0'})
        cherrypy.config.update({'server.socket_port': port})

        cherrypy.engine.start()
        cherrypy.engine.block()


if __name__ == "__main__":
    adaptor = ThingSpeakAdaptor()
    adaptor.start()
