import os
import sys
import time
import math
import json
import random
import string
import threading
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import cherrypy
import requests
import paho.mqtt.client as mqtt

# --- shared environment model -------------------------------------------------
# Every gateway derives the outdoor conditions from the same wall-clock based
# function, so all zones stay in phase without exchanging any messages.
DAY_MINUTES = 16.0           # one simulated day per this many real minutes
OUT_TEMP_MEAN = 18.0
OUT_TEMP_AMP = 8.0          # outdoor temp swings roughly 10..26 C
OUT_HUM_MEAN = 55.0
OUT_HUM_SLOPE = 2.5         # humidity moves opposite to temperature
LIGHT_PEAK = 5000.0

# Indoor thermal model.
BASE_LEAK = 0.03            # coupling to outdoor when the window is closed
OPEN_LEAK = 0.30           # extra coupling when fully open
SOLAR_MAX = 0.09           # solar gain warms the room during the day
GLASS_BASE = 0.25          # fraction of daylight passing a closed window
OPEN_LIGHT = 0.50          # extra daylight let in when fully open
INDOOR_LIGHT_BASE = 150.0  # artificial indoor lighting

# SenML unit symbols (RFC 8428) for each measurement we publish.
SENML_UNITS = {"temp": "Cel", "hum": "%RH", "light": "lx"}


def clamp(value, low, high):
    return max(low, min(high, value))


def sim_hour():
    day_seconds = DAY_MINUTES * 60.0
    return (time.time() % day_seconds) / day_seconds * 24.0


def outdoor_temp_ref(hour):
    return OUT_TEMP_MEAN + OUT_TEMP_AMP * math.sin(2 * math.pi * (hour - 10) / 24.0)


def outdoor_light_ref(hour):
    if 6.0 <= hour <= 18.0:
        return LIGHT_PEAK * math.sin(math.pi * (hour - 6.0) / 12.0)
    return 0.0


def outdoor_hum_ref(hour):
    return clamp(OUT_HUM_MEAN - OUT_HUM_SLOPE * (outdoor_temp_ref(hour) - OUT_TEMP_MEAN), 20.0, 95.0)


class GatewayAPI(object):
    """Device-level REST interface: read the current state, or set the window."""
    exposed = True

    def __init__(self, gateway):
        self.gateway = gateway

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        gw = self.gateway
        return {
            "deviceID": gw.device_id,
            "zone": gw.zone_name,
            "readings": gw.last_reading,
            "window": gw.window_position if gw.has_actuator else None,
        }

    @cherrypy.tools.json_out()
    def PUT(self, *uri, **params):
        gw = self.gateway
        if not gw.has_actuator or uri[:1] != ("window",):
            raise cherrypy.HTTPError(404, "This device has no window to set")

        body = json.loads(cherrypy.request.body.read() or b"{}")
        position = body.get("position")
        if not isinstance(position, (int, float)) or not 0 <= position <= 100:
            raise cherrypy.HTTPError(400, 'Body must be {"position": 0-100}')

        # Send it through MQTT like any other command, so the analyzer, the
        # dashboard and the bot all see the change too.
        gw.publish_command(int(position), "REST request")
        return {"message": f"window set to {int(position)}%"}


class Gateway:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.client = None

        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        safe_zone = self.zone_name.replace(" ", "_").lower()
        self.device_id = f"gw_{safe_zone}_{random_suffix}"
        self.topic_base = self.zone_name.replace(" ", "").lower()
        self.is_outdoor = self.zone_name == "Outdoor"

        # Current window opening 0..100 (indoor zones only) and indoor state.
        self.window_position = 0
        self.last_reading = {}
        h = sim_hour()
        self.indoor_temp = clamp(outdoor_temp_ref(h) + 2.0, 10.0, 40.0)
        self.indoor_hum = outdoor_hum_ref(h)

        self.my_topics = [f"home/{self.topic_base}/sensor/{s}" for s in self.sensors]
        if self.has_actuator:
            self.my_topics.append(f"home/{self.topic_base}/actuator/window")

        self.device_info = {
            "deviceID": self.device_id,
            "deviceName": f"{self.zone_name} Gateway",
            "deviceType": "rpi_connector",
            "zone": self.zone_name,
            "measureType": self.sensors,
            "hasActuator": self.has_actuator,
            "availableServices": ["MQTT", "REST"],
            "servicesDetails": [
                {"serviceType": "MQTT", "topic": self.my_topics},
                {"serviceType": "REST", "serviceIP": self.service_ip,
                 "endpoints": ["/", "/window"] if self.has_actuator else ["/"]}
            ]
        }

    def load_settings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.zone_name = settings.get("zone_name", "Unknown Zone")
                self.sensors = settings.get("sensors", [])
                self.has_actuator = settings.get("has_actuator", False)
                self.service_ip = settings.get("service_ip", "localhost:8090")
        except FileNotFoundError:
            print(f"[{filepath}] Configuration file not found.")
            sys.exit(1)

    def discover_and_register(self):
        print(f"[{self.zone_name}] Contacting registry at {self.registry_url}...")
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/broker", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["IP"]
                    self.port = config["port"]
                    self.transport = config["transport"]
                    print(f"[{self.zone_name}] Broker config received: {self.broker}:{self.port}")
                    break
            except Exception:
                time.sleep(3)

        try:
            resp = requests.post(f"{self.registry_url}/devicesList", json=self.device_info)
            if resp.status_code == 200:
                print(f"[{self.zone_name}] Registered in catalog.")
        except Exception as e:
            print(f"[{self.zone_name}] Registration failed: {e}")

    def heartbeat_worker(self):
        while True:
            time.sleep(50)
            try:
                resp = requests.put(f"{self.registry_url}/devicesList", json={"id": self.device_id}, timeout=5)
                if resp.status_code == 404:
                    # Our entry expired (e.g. the registry was down for a while): register again.
                    requests.post(f"{self.registry_url}/devicesList", json=self.device_info, timeout=5)
            except Exception:
                pass

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[{self.zone_name}] Connected to MQTT broker.")
            if self.has_actuator:
                self.client.subscribe(f"home/{self.topic_base}/actuator/window")
        else:
            print(f"[{self.zone_name}] MQTT connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        # The actuator reports its new opening; the physics reacts to it.
        try:
            payload = json.loads(msg.payload.decode())
            if not isinstance(payload, dict):
                return
            position = payload.get("position")
            if position is not None:
                self.window_position = int(clamp(position, 0, 100))
                print(f"[{self.zone_name}] Window set to {self.window_position}%")
        except json.JSONDecodeError:
            pass

    # --- simulated physics --------------------------------------------------

    def sample_outdoor(self):
        h = sim_hour()
        temp = outdoor_temp_ref(h) + random.uniform(-0.3, 0.3)
        light = max(0.0, outdoor_light_ref(h) + random.uniform(-80, 80))
        hum = clamp(outdoor_hum_ref(h) + random.uniform(-2, 2), 20.0, 95.0)
        return {"temp": round(temp, 2), "hum": round(hum, 2), "light": round(light, 2)}

    def sample_indoor(self):
        h = sim_hour()
        oref = outdoor_temp_ref(h)
        pos = self.window_position / 100.0
        leak = BASE_LEAK + OPEN_LEAK * pos
        daylight = outdoor_light_ref(h) / LIGHT_PEAK

        # Room drifts toward outdoor (faster when open) and is warmed by the sun.
        solar = SOLAR_MAX * daylight * (1.0 - 0.4 * pos)
        self.indoor_temp = clamp(
            self.indoor_temp + leak * (oref - self.indoor_temp) + solar + random.uniform(-0.05, 0.05),
            10.0, 40.0)
        self.indoor_hum = clamp(
            self.indoor_hum + leak * (outdoor_hum_ref(h) - self.indoor_hum) + random.uniform(-0.5, 0.5),
            20.0, 90.0)
        light = outdoor_light_ref(h) * (GLASS_BASE + OPEN_LIGHT * pos) + INDOOR_LIGHT_BASE
        return {"temp": round(self.indoor_temp, 2), "hum": round(self.indoor_hum, 2),
                "light": round(max(0.0, light + random.uniform(-30, 30)), 2)}

    def publish_command(self, position, reason):
        payload = json.dumps({"position": position, "reason": reason, "source": "manual"})
        self.client.publish(f"home/{self.topic_base}/actuator/window", payload, retain=True)

    def senml(self, sensor, value):
        """Wrap a reading in a SenML document (RFC 8428)."""
        return json.dumps({
            "bn": f"home/{self.topic_base}/",
            "e": [{
                "n": sensor,
                "u": SENML_UNITS.get(sensor, ""),
                "t": int(time.time()),
                "v": value,
            }]
        })

    def start(self):
        self.discover_and_register()
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()

        self.client = mqtt.Client(client_id=f"MQTT_{self.device_id}", transport=self.transport)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()

        # The REST server runs in the background; the main thread keeps sampling.
        conf = {'/': {'request.dispatch': cherrypy.dispatch.MethodDispatcher()}}
        cherrypy.tree.mount(GatewayAPI(self), '/', conf)
        cherrypy.config.update({
            'server.socket_host': '0.0.0.0',
            'server.socket_port': int(self.service_ip.split(":")[1]),
            'engine.autoreload.on': False,
        })
        cherrypy.engine.start()

        try:
            while True:
                reading = self.sample_outdoor() if self.is_outdoor else self.sample_indoor()
                self.last_reading = reading
                published = []
                for sensor in self.sensors:
                    value = reading[sensor]
                    topic = f"home/{self.topic_base}/sensor/{sensor}"
                    self.client.publish(topic, self.senml(sensor, value))
                    published.append(f"{sensor}: {value}")

                if published:
                    tag = "" if self.is_outdoor else f" | window {self.window_position}%"
                    print(f"[{self.zone_name}] " + " | ".join(published) + tag)

                time.sleep(5)
        except KeyboardInterrupt:
            cherrypy.engine.exit()
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    config_file = os.environ.get("CONFIG_FILE", "settings_livingroom.json")
    Gateway(config_file).start()
