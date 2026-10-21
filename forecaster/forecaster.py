import sys
import time
import json
import random
import string
import calendar
import threading
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import cherrypy
import requests
import numpy as np
import paho.mqtt.client as mqtt
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error

FEATURES = ["outdoor_gap", "window_exchange", "sunlight"]


def cors():
    cherrypy.response.headers["Access-Control-Allow-Origin"] = "*"
    cherrypy.response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    cherrypy.response.headers["Access-Control-Allow-Headers"] = "Content-Type"

cherrypy.tools.cors = cherrypy.Tool('before_handler', cors)


def senml_value(payload):
    """Extract the first measurement value from a SenML document (RFC 8428)."""
    records = payload.get("e")
    if isinstance(records, list) and records:
        return records[0].get("v")
    return None


def to_epoch(stamp):
    # ThingSpeak timestamps are UTC, e.g. "2026-07-16T13:22:54Z". timegm keeps
    # them in UTC whatever the container's TZ is.
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def make_features(temp, out_temp, window, light):
    """The three inputs of the model: Newton's law of cooling through the walls,
    extra exchange through the open window, and solar gain. Each one has a
    physical meaning, so a linear model is enough and its coefficients can be
    read directly."""
    gap = out_temp - temp
    return [
        gap,                     # how much warmer or colder it is outside
        window / 100.0 * gap,    # extra exchange through the open window
        light / 1000.0,          # sunlight warming the room, in kilolux
    ]


class ForecasterAPI(object):
    """REST interface: the latest forecasts, and how good each model is."""
    exposed = True

    def __init__(self, forecaster):
        self.forecaster = forecaster

    @cherrypy.tools.json_out()
    def OPTIONS(self, *uri, **params):
        return {"status": "OK"}

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        fc = self.forecaster
        if len(uri) > 0 and uri[0] == "model":
            return {zone: {k: v for k, v in info.items() if k != "model"}
                    for zone, info in fc.models.items()}
        if len(uri) > 0 and uri[0] == "forecast":
            return fc.forecasts
        return {"message": "Temperature forecaster. Use GET /model or GET /forecast."}


class Forecaster:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.client = None

        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        self.service_id = f"forecaster_{random_suffix}"

        self.models = {}        # zone -> trained model and its scores
        self.forecasts = {}     # zone -> latest published forecast
        self.latest = {}        # zone -> {reading: value}, live from MQTT
        self.step_seconds = 20.0

        self.service_info = {
            "serviceID": self.service_id,
            "serviceName": self.service_name,
            "availableServices": ["MQTT", "REST"],
            "servicesDetails": [
                {
                    "serviceType": "MQTT",
                    "topic": ["home/+/sensor/+", "home/+/actuator/window", "home/+/forecast/temp"]
                },
                {
                    "serviceType": "REST",
                    "serviceIP": self.service_ip,
                    "endpoints": ["/model", "/forecast"]
                }
            ]
        }

    def load_settings(self, filepath):
        defaults = {
            "history_results": 2000, "history_hours": 6, "min_samples": 60,
            "horizon_steps": 3, "max_step_seconds": 60,
            "test_fraction": 0.2, "retrain_minutes": 10, "publish_seconds": 10,
        }
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.service_name = settings.get("service_name", "Temperature Forecaster")
                self.service_ip = settings.get("service_ip", "forecaster:8082")
                self.outdoor_zone = settings.get("outdoor_zone", "outdoor")
                self.cfg = {**defaults, **settings.get("model", {})}
        except FileNotFoundError:
            print("Forecaster settings.json not found.")
            sys.exit(1)

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
                    print(f"Broker config received: {self.broker}:{self.port}")
                    break
            except Exception:
                time.sleep(3)

        try:
            resp = requests.post(f"{self.registry_url}/servicesList", json=self.service_info)
            if resp.status_code == 200:
                print("Forecaster registered in catalog.")
        except Exception as e:
            print(f"Registration failed: {e}")

    def heartbeat_worker(self):
        while True:
            time.sleep(50)
            try:
                requests.put(f"{self.registry_url}/servicesList", json={"id": self.service_id}, timeout=5)
            except Exception:
                pass

    def find_history_url(self):
        # Ask the catalog who serves stored history instead of hard-coding the
        # adaptor's address.
        services = requests.get(f"{self.registry_url}/servicesList", timeout=5).json()
        for service in services:
            for detail in service.get("servicesDetails", []):
                if detail.get("serviceType") == "REST" and "/history" in detail.get("endpoints", []):
                    return f"http://{detail['serviceIP']}/history"
        return None

    # --- training -----------------------------------------------------------

    def build_dataset(self, samples, zone):
        """Turn the history into (features, target) pairs for one room. The
        target is how much the temperature changes over the next few steps."""
        h = self.cfg["horizon_steps"]
        rows = []
        for s in samples:
            room = s["zones"].get(zone, {})
            outside = s["zones"].get(self.outdoor_zone, {})
            if "temp" in room and "temp" in outside:
                rows.append((to_epoch(s["t"]), room["temp"], outside["temp"],
                             room.get("window", 0.0), room.get("light", 0.0)))

        X, y = [], []
        for i in range(len(rows) - h):
            # Skip any stretch that spans a gap (the system was switched off).
            if rows[i + h][0] - rows[i][0] > h * self.cfg["max_step_seconds"]:
                continue
            _, temp, out_temp, window, light = rows[i]
            # Only learn from stretches where nobody moved the window. The data
            # comes from the running system, so otherwise the model would learn
            # the analyzer's future reactions instead of how the room behaves.
            if any(rows[j][3] != window for j in range(i, i + h + 1)):
                continue
            X.append(make_features(temp, out_temp, window, light))
            y.append(rows[i + h][1] - temp)
        return np.array(X), np.array(y)

    def train_zone(self, zone, samples):
        X, y = self.build_dataset(samples, zone)
        if len(y) < self.cfg["min_samples"]:
            return None

        # Time series: train on the older part and test on the most recent part.
        # Shuffling would let the test peek into the future.
        split = int(len(y) * (1 - self.cfg["test_fraction"]))
        model = LinearRegression().fit(X[:split], y[:split])
        mae = mean_absolute_error(y[split:], model.predict(X[split:]))
        # Naive baseline: "the temperature will stay where it is".
        mae_naive = mean_absolute_error(y[split:], np.zeros(len(y) - split))

        # The live model is refitted on all the data.
        model = LinearRegression().fit(X, y)
        return {
            "model": model,
            "samples": len(y),
            "mae": round(float(mae), 3),
            "mae_naive": round(float(mae_naive), 3),
            "usable": bool(mae < mae_naive),
            "coefficients": {name: round(float(c), 4) for name, c in zip(FEATURES, model.coef_)},
            "intercept": round(float(model.intercept_), 4),
            "horizon_s": int(self.cfg["horizon_steps"] * self.step_seconds),
            "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def retrain(self):
        url = self.find_history_url()
        if url is None:
            print("No history provider in the catalog yet.")
            return
        resp = requests.get(url, params={"results": self.cfg["history_results"]}, timeout=60)
        resp.raise_for_status()

        # Only recent data: older runs may come from a different simulator.
        cutoff = time.time() - self.cfg["history_hours"] * 3600
        samples = [s for s in resp.json()["samples"] if to_epoch(s["t"]) >= cutoff]
        if len(samples) > 1:
            times = [to_epoch(s["t"]) for s in samples]
            steps = sorted(b - a for a, b in zip(times, times[1:]))
            self.step_seconds = float(steps[len(steps) // 2])

        zones = sorted({z for s in samples for z in s["zones"] if z != self.outdoor_zone})
        if not zones:
            print("No recent history yet, waiting for data.")
        models = {}
        for zone in zones:
            info = self.train_zone(zone, samples)
            if info is None:
                print(f"[{zone}] not enough recent history yet ({len(samples)} samples), skipping")
                continue
            models[zone] = info
            verdict = "in use" if info["usable"] else "not better than naive, held back"
            print(f"[{zone}] trained on {info['samples']} samples: MAE {info['mae']} C "
                  f"vs naive {info['mae_naive']} C ({verdict})")
        self.models = models

    def training_worker(self):
        while True:
            try:
                self.retrain()
            except Exception as e:
                print(f"Training skipped: {e}")
            # Retry soon while there is no model yet, then settle to the normal pace.
            time.sleep(self.cfg["retrain_minutes"] * 60 if self.models else 120)

    # --- live forecasting ---------------------------------------------------

    def predict(self, zone, info):
        room = self.latest.get(zone, {})
        outside = self.latest.get(self.outdoor_zone, {})
        if "temp" not in room or "temp" not in outside:
            return None
        x = make_features(room["temp"], outside["temp"], room.get("window", 0.0), room.get("light", 0.0))
        change = info["model"].predict(np.array([x]))[0]
        return round(room["temp"] + float(change), 2)

    def publish_worker(self):
        while True:
            time.sleep(self.cfg["publish_seconds"])
            horizon = self.cfg["horizon_steps"] * self.step_seconds
            for zone, info in list(self.models.items()):
                if not info["usable"]:
                    continue  # a model that cannot beat the naive guess stays quiet
                value = self.predict(zone, info)
                if value is None:
                    continue
                target_time = int(time.time() + horizon)
                self.forecasts[zone] = {"value": value, "for": target_time, "horizon_s": int(horizon)}
                # SenML like the sensors; "t" is the moment the forecast is for.
                payload = json.dumps({
                    "bn": f"home/{zone}/",
                    "e": [{"n": "temp_forecast", "u": "Cel", "t": target_time, "v": value}]
                })
                self.client.publish(f"home/{zone}/forecast/temp", payload)

    # --- mqtt ---------------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Forecaster connected to MQTT broker.")
            self.client.subscribe("home/+/sensor/+")
            self.client.subscribe("home/+/actuator/window")
        else:
            print(f"MQTT connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            if not isinstance(payload, dict):
                return
            parts = msg.topic.split('/')
            if len(parts) < 4:
                return
            zone, kind, name = parts[1], parts[2], parts[3]

            if kind == "sensor":
                value = senml_value(payload)
                if not isinstance(value, (int, float)):
                    return
                self.latest.setdefault(zone, {})[name] = float(value)
            elif kind == "actuator" and isinstance(payload.get("position"), (int, float)):
                self.latest.setdefault(zone, {})["window"] = float(payload["position"])
        except json.JSONDecodeError:
            pass

    def start(self):
        self.discover_and_register()
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()

        self.client = mqtt.Client(client_id=f"MQTT_{self.service_id}", transport=self.transport)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        print("Connecting to broker...")
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()

        threading.Thread(target=self.training_worker, daemon=True).start()
        threading.Thread(target=self.publish_worker, daemon=True).start()

        port = int(self.service_ip.split(":")[1])
        print(f"Starting forecaster REST server on port {port}...")
        conf = {
            '/': {
                'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
                'tools.cors.on': True
            }
        }
        cherrypy.tree.mount(ForecasterAPI(self), '/', conf)
        cherrypy.config.update({'server.socket_host': '0.0.0.0'})
        cherrypy.config.update({'server.socket_port': port})

        cherrypy.engine.start()
        cherrypy.engine.block()


if __name__ == "__main__":
    Forecaster().start()
