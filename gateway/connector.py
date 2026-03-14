import time
import random
import string
import json
import requests
import threading
import sys
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning) 

import paho.mqtt.client as mqtt

class AdvancedGateway:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.client = None
        
        # 1. ساختن یک ID و Client ID کاملاً یکتا (استاندارد استاد رافائل)
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        safe_zone = self.zone_name.replace(" ", "_").lower()
        self.device_id = f"gw_{safe_zone}_{random_suffix}"
        self.topic_base = self.zone_name.replace(" ", "").lower()

        # 2. ساختاردهی تاپیک‌ها برای معرفی به کاتالوگ
        self.my_topics = []
        for sensor in self.sensors:
            self.my_topics.append(f"home/{self.topic_base}/sensor/{sensor}")
        
        if self.has_actuator:
            self.my_topics.append(f"home/{self.topic_base}/actuator/window")

        # 3. پروفایل دیوایس دقیقاً مشابه فایل catalog.json استاد
        self.device_info = {
            "deviceID": self.device_id,
            "deviceName": f"{self.zone_name} Gateway",
            "measureType": self.sensors,
            "availableServices": ["MQTT"],
            "servicesDetails": [
                {
                    "serviceType": "MQTT",
                    "topic": self.my_topics
                }
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
        except FileNotFoundError:
            print(f"❌ FATAL ERROR: Configuration file '{filepath}' not found!")
            sys.exit(1)

    def discover_and_register(self):
        print(f"🔍 [{self.zone_name}] Contacting Registry at {self.registry_url}...")
        
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/broker", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["IP"]
                    self.port = config["port"]
                    self.transport = config["transport"]
                    print(f"✅ [{self.zone_name}] Broker config received: {self.broker}:{self.port}")
                    break
            except Exception:
                time.sleep(3)

        # ثبت نام دیوایس در مسیر جدید (devicesList)
        try:
            resp = requests.post(f"{self.registry_url}/devicesList", json=self.device_info)
            if resp.status_code == 200:
                print(f"✅ [{self.zone_name}] Registered successfully in Catalog!")
        except Exception as e:
            print(f"❌ [{self.zone_name}] Registration failed: {e}")

    def heartbeat_worker(self):
        """این تابع هر 50 ثانیه یک PUT می‌فرسته تا بگه دستگاه هنوز زنده‌ست"""
        while True:
            time.sleep(50)
            try:
                requests.put(
                    f"{self.registry_url}/devicesList", 
                    json={"id": self.device_id},
                    timeout=5
                )
                # (برای شلوغ نشدن ترمینال، پیام موفقیت heartbeat رو پرینت نمی‌کنیم)
            except Exception:
                pass

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✅ [{self.zone_name}] Connected to MQTT Broker!")
            if self.has_actuator:
                topic = f"home/{self.topic_base}/actuator/window"
                self.client.subscribe(topic)
        else:
            print(f"❌ Connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        payload = json.loads(msg.payload.decode())
        command = payload.get("command")
        if command == "OPEN":
            print(f"\n🪟 🟢 [{self.zone_name}] Window Opening!\n")
        elif command == "CLOSE":
            print(f"\n🪟 🔴 [{self.zone_name}] Window Closing!\n")

    def start(self):
        self.discover_and_register()
        
        # استارت کردن سیستم ضربان قلب (Heartbeat)
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()

        self.client = mqtt.Client(client_id=f"MQTT_{self.device_id}", transport=self.transport)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()
        
        try:
            while True:
                published_data = []
                if "temp" in self.sensors:
                    val = round(random.uniform(10.0, 35.0) if self.zone_name == "Outdoor" else random.uniform(20.0, 28.0), 2)
                    self.client.publish(f"home/{self.topic_base}/sensor/temp", json.dumps({"value": val, "unit": "C"}))
                    published_data.append(f"Temp: {val}")
                if "hum" in self.sensors:
                    val = round(random.uniform(20.0, 80.0) if self.zone_name == "Outdoor" else round(random.uniform(30.0, 60.0), 2), 2)
                    self.client.publish(f"home/{self.topic_base}/sensor/hum", json.dumps({"value": val, "unit": "%"}))
                    published_data.append(f"Hum: {val}")
                if "light" in self.sensors:
                    val = round(random.uniform(100, 5000) if self.zone_name == "Outdoor" else round(random.uniform(200, 800), 2), 2)
                    self.client.publish(f"home/{self.topic_base}/sensor/light", json.dumps({"value": val, "unit": "lux"}))
                    published_data.append(f"Light: {val}")
                
                if published_data:
                    print(f"📤 [{self.zone_name}] Data -> " + " | ".join(published_data))
                
                time.sleep(5)
        except KeyboardInterrupt:
            self.client.loop_stop()
            self.client.disconnect()

if __name__ == "__main__":
    import os
    config_file = os.environ.get("CONFIG_FILE", "settings_livingroom.json")
    gateway = AdvancedGateway(config_file)
    gateway.start()