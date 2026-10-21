import sys
import time
import json
import random
import string
import threading
import warnings
from collections import deque
warnings.filterwarnings("ignore", category=DeprecationWarning)

import requests
import paho.mqtt.client as mqtt

# Physically plausible bounds; readings outside these are treated as glitches.
SENSOR_BOUNDS = {"temp": (-20.0, 60.0), "hum": (0.0, 100.0), "light": (0.0, 20000.0)}
# Maximum believable jump between two consecutive samples of the same sensor.
MAX_JUMP = {"temp": 15.0, "hum": 40.0, "light": 9000.0}


def senml_value(payload):
    """Extract the first measurement value from a SenML document (RFC 8428)."""
    records = payload.get("e")
    if isinstance(records, list) and records:
        return records[0].get("v")
    return None


class Analyzer:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.client = None

        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        self.service_id = f"analyzer_{random_suffix}"

        cfg = self.control
        self.window_size = int(cfg["window_size"])

        # Sliding sample windows per zone and sensor.
        self.history = {}
        # Smoothed target-position buffer per zone (enforces decision persistence).
        self.target_hist = {}
        # Last position we actually commanded per zone.
        self.commanded = {}
        # Zones under manual override until this timestamp are left alone.
        self.override_until = {}
        # Latest forecast per zone from the forecaster: (value, received at).
        self.forecasts = {}

        self.service_info = {
            "serviceID": self.service_id,
            "serviceName": self.service_name,
            "availableServices": ["MQTT"],
            "servicesDetails": [
                {
                    "serviceType": "MQTT",
                    "topic": ["home/+/sensor/+", "home/+/actuator/window", "home/+/forecast/temp"]
                }
            ]
        }

    def load_settings(self, filepath):
        defaults = {
            "temp_comfort_min": 22.0, "temp_comfort_max": 26.0,
            "hum_comfort_min": 30.0, "hum_comfort_max": 65.0,
            "light_glare": 1200.0, "light_low": 250.0, "light_release": 400.0,
            "daylight_min": 300.0, "outdoor_delta": 1.0,
            "window_size": 6, "min_samples": 3,
            "max_step": 20, "deadband": 8, "persistence": 3,
            "manual_override_sec": 90, "forecast_max_age_sec": 60,
        }
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.service_name = settings.get("service_name", "Analyzer")
                self.outdoor_zone = settings.get("outdoor_zone", "outdoor")
                self.control = {**defaults, **settings.get("control", {})}
        except FileNotFoundError:
            print("Analyzer settings.json not found.")
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
                print("Analyzer registered in catalog.")
        except Exception as e:
            print(f"Registration failed: {e}")

    def heartbeat_worker(self):
        while True:
            time.sleep(50)
            try:
                requests.put(f"{self.registry_url}/servicesList", json={"id": self.service_id}, timeout=5)
            except Exception:
                pass

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Analyzer connected to MQTT broker.")
            self.client.subscribe("home/+/sensor/+")
            self.client.subscribe("home/+/actuator/window")
            self.client.subscribe("home/+/forecast/temp")
        else:
            print(f"MQTT connection failed: {rc}")

    # --- data pipeline ------------------------------------------------------

    def store_sample(self, zone, sensor, value):
        """Validate and store a reading; drop values that fail a sanity or
        jump check so a single glitch cannot swing the decision."""
        if not isinstance(value, (int, float)):
            return False
        low, high = SENSOR_BOUNDS.get(sensor, (float("-inf"), float("inf")))
        if not (low <= value <= high):
            return False

        window = self.history.setdefault(zone, {}).setdefault(sensor, deque(maxlen=self.window_size))
        if window and abs(value - window[-1]) > MAX_JUMP.get(sensor, float("inf")):
            return False  # implausible jump, likely noise

        window.append(float(value))
        return True

    def average(self, zone, sensor):
        window = self.history.get(zone, {}).get(sensor)
        return sum(window) / len(window) if window else None

    def trend(self, zone, sensor):
        """Difference between the recent half and the older half of the window;
        positive means the value is rising."""
        window = self.history.get(zone, {}).get(sensor)
        if not window or len(window) < 4:
            return 0.0
        half = len(window) // 2
        older = list(window)[:half]
        recent = list(window)[half:]
        return sum(recent) / len(recent) - sum(older) / len(older)

    def fresh_forecast(self, zone):
        value, received = self.forecasts.get(zone, (None, 0))
        if time.time() - received > self.control["forecast_max_age_sec"]:
            return None
        return value

    # --- control law --------------------------------------------------------

    def compute_target(self, zone):
        """Return (target_position 0-100, reasons, temp_trend) or None if there
        is not yet enough data to decide."""
        cfg = self.control
        tavg = self.average(zone, "temp")
        havg = self.average(zone, "hum")
        lavg = self.average(zone, "light")
        oavg = self.average(self.outdoor_zone, "temp")
        olight = self.average(self.outdoor_zone, "light")

        temp_window = self.history.get(zone, {}).get("temp", [])
        if tavg is None or len(temp_window) < cfg["min_samples"] or oavg is None:
            return None

        ttrend = self.trend(zone, "temp")
        target = 0.0
        reasons = []

        # A fresh forecast that says the room is about to leave the comfort band
        # lets us act now instead of after it happens.
        forecast = self.fresh_forecast(zone)
        tdec = tavg
        if forecast is not None:
            if forecast > cfg["temp_comfort_max"] and forecast > tavg:
                tdec = forecast
            elif forecast < cfg["temp_comfort_min"] and forecast < tavg:
                tdec = forecast

        # 1) Temperature is the high-priority driver, compared against outdoor.
        #    keep_closed means opening would move the room further from comfort.
        keep_closed = False
        if tdec > cfg["temp_comfort_max"]:
            over = tdec - cfg["temp_comfort_max"]
            if oavg < tdec - cfg["outdoor_delta"]:
                target = 30 + over * 12 + (tdec - oavg) * 4
                reasons.append(f"warm inside {tdec:.1f}C, cooler outside {oavg:.1f}C -> open for cooling")
            else:
                keep_closed = True
                reasons.append(f"warm inside {tdec:.1f}C but outside {oavg:.1f}C not cool enough -> close")
        elif tdec < cfg["temp_comfort_min"]:
            under = cfg["temp_comfort_min"] - tdec
            if oavg > tdec + cfg["outdoor_delta"]:
                target = 25 + under * 12 + (oavg - tdec) * 3
                reasons.append(f"cool inside {tdec:.1f}C, warmer outside {oavg:.1f}C -> open to warm")
            else:
                keep_closed = True
                reasons.append(f"cool inside {tdec:.1f}C but outside {oavg:.1f}C not warm enough -> close")
        else:
            reasons.append(f"temperature comfortable {tavg:.1f}C")
        if tdec != tavg:
            reasons[0] += " (forecast, acting early)"

        # 2) Humidity can call for ventilation on its own, but not against the
        #    temperature rule.
        if havg is not None:
            if havg > cfg["hum_comfort_max"] and keep_closed:
                reasons.append(f"humidity high {havg:.0f}% but temperature has priority")
            elif havg > cfg["hum_comfort_max"]:
                vent = 35 + (havg - cfg["hum_comfort_max"]) * 2
                target = max(target, vent)
                reasons.append(f"humidity high {havg:.0f}% -> ventilate")
            elif havg < cfg["hum_comfort_min"] and tavg <= cfg["temp_comfort_min"]:
                target = 0
                reasons.append(f"air dry {havg:.0f}% and cool -> stay closed")

        # 3) A dark room on a bright day opens a little to let daylight in. Once
        #    open it only closes above a higher threshold (hysteresis), so the
        #    extra light coming in does not make the window flap.
        dark_limit = cfg["light_release"] if self.commanded.get(zone, 0) > 0 else cfg["light_low"]
        if (target == 0 and lavg is not None and olight is not None
                and lavg < dark_limit and olight > cfg["daylight_min"]
                and cfg["temp_comfort_min"] <= tdec <= cfg["temp_comfort_max"]):
            target = 25
            reasons.append(f"room dark ({lavg:.0f}lx) on a bright day -> let daylight in")

        # 4) Strong light only caps how far we open (glare / solar gain).
        if lavg is not None and lavg > cfg["light_glare"] and target > 40:
            target = 40
            reasons.append(f"strong light {lavg:.0f}lx -> limit opening")

        return max(0.0, min(100.0, target)), reasons, ttrend

    def evaluate(self, zone):
        if zone == self.outdoor_zone:
            return  # outdoor has no window

        result = self.compute_target(zone)
        if result is None:
            return
        target, reasons, ttrend = result
        cfg = self.control

        # Persistence: act on the average of the last few targets, so a brief
        # spike does not move the window.
        tq = self.target_hist.setdefault(zone, deque(maxlen=int(cfg["persistence"])))
        tq.append(target)
        smoothed = sum(tq) / len(tq)

        if time.time() < self.override_until.get(zone, 0):
            return  # user is in manual control

        current = self.commanded.get(zone, 0)
        step = max(-cfg["max_step"], min(cfg["max_step"], smoothed - current))
        new_pos = int(round(max(0, min(100, current + step))))

        # Only command a change that is meaningful, or that reaches a limit.
        moved_enough = abs(new_pos - current) >= cfg["deadband"]
        hit_limit = new_pos in (0, 100) and new_pos != current
        if not (moved_enough or hit_limit):
            return

        if ttrend > 0.4:
            reasons.append("temperature rising")
        elif ttrend < -0.4:
            reasons.append("temperature falling")

        self.commanded[zone] = new_pos
        reason = "; ".join(reasons)
        payload = json.dumps({"position": new_pos, "reason": reason, "source": "auto"})
        # Retained, so anyone who connects later learns the current position.
        self.client.publish(f"home/{zone}/actuator/window", payload, retain=True)
        print(f"[{zone}] window -> {new_pos}% ({reason})")

    # --- mqtt callbacks -----------------------------------------------------

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            if not isinstance(payload, dict):
                return  # ignore foreign/malformed traffic on the shared broker
            parts = msg.topic.split('/')
            zone = parts[1]
            kind = parts[2]

            if kind == "forecast":
                value = senml_value(payload)
                if isinstance(value, (int, float)):
                    self.forecasts[zone] = (float(value), time.time())
                return

            if kind == "actuator":
                # Every command tells us where the window is (the retained one
                # too, right after a restart). Only a live manual command
                # pauses automatic control for this zone.
                position = payload.get("position")
                if isinstance(position, (int, float)):
                    self.commanded[zone] = int(position)
                if payload.get("source") == "manual" and not msg.retain:
                    self.override_until[zone] = time.time() + self.control["manual_override_sec"]
                return

            sensor = parts[3]
            if not self.store_sample(zone, sensor, senml_value(payload)):
                return

            # Re-evaluate once per cycle, driven by the high-priority temp topic.
            if sensor == "temp":
                self.evaluate(zone)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"Error processing message: {e}")

    def start(self):
        self.discover_and_register()
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()

        self.client = mqtt.Client(client_id=f"MQTT_{self.service_id}", transport=self.transport)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        print("Connecting to broker...")
        self.client.connect(self.broker, self.port, 60)

        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            print("Analyzer stopped.")
            self.client.disconnect()


if __name__ == "__main__":
    Analyzer().start()
