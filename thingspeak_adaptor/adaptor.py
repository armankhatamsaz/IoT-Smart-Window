import time
import json
import requests
import threading
import random
import string
import sys
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import paho.mqtt.client as mqtt

class MultiChannelThingSpeakAdaptor:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        
        # شناسه یکتا
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        self.service_id = f"ts_adaptor_{random_suffix}"

        self.service_info = {
            "serviceID": self.service_id,
            "serviceName": "ThingSpeak Cloud Logger",
            "availableServices": ["MQTT"],
            "servicesDetails": [
                {
                    "serviceType": "MQTT",
                    "topic": ["home/+/sensor/+"]
                }
            ]
        }

        self.buffers = {channel["api_key"]: {} for channel in self.channels}

    def load_settings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.channels = settings.get("channels", [])
        except FileNotFoundError:
            print("❌ FATAL ERROR: settings.json not found for Adaptor!")
            sys.exit(1)

    def discover_and_register(self):
        print(f"🔍 Contacting Registry at {self.registry_url}...")
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/broker", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["IP"]
                    self.port = config["port"]
                    self.transport = config["transport"]
                    print("✅ Registry Config received!")
                    break
            except Exception:
                time.sleep(3)
                
        try:
            resp = requests.post(f"{self.registry_url}/servicesList", json=self.service_info)
            if resp.status_code == 200:
                print("✅ TS Adaptor registered in Catalog!")
        except Exception as e:
            print(f"❌ Registration failed: {e}")

    def heartbeat_worker(self):
        """تپش قلب هر 50 ثانیه"""
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
            print("✅ Adaptor Connected to MQTT Broker!")
            for channel in self.channels:
                for topic in channel["mapping"].keys():
                    client.subscribe(topic)
        else:
            print(f"❌ Connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode())
            
            for channel in self.channels:
                if topic in channel["mapping"]:
                    field_name = channel["mapping"][topic]
                    api_key = channel["api_key"]
                    self.buffers[api_key][field_name] = payload.get("value")
        except json.JSONDecodeError:
            pass

    def thingspeak_worker(self):
        url = "https://api.thingspeak.com/update"
        while True:
            time.sleep(20) # دور زدن محدودیت 15 ثانیه‌ای تینگ‌اسپیک
            
            for channel in self.channels:
                api_key = channel["api_key"]
                channel_name = channel["name"]
                buffer_data = self.buffers[api_key]
                
                if buffer_data:
                    data = {"api_key": api_key}
                    data.update(buffer_data)
                    
                    try:
                        response = requests.post(url, data=data, timeout=10)
                        if response.status_code == 200:
                            print(f"☁️ [{channel_name}] Uploaded successfully!")
                        else:
                            print(f"⚠️ [{channel_name}] Upload failed. HTTP: {response.status_code}")
                    except Exception as e:
                        pass
                    
                    self.buffers[api_key].clear()

    def start(self):
        self.discover_and_register()
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()

        self.client = mqtt.Client(client_id=f"MQTT_{self.service_id}", transport=self.transport)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        print("⏳ Connecting to Broker...")
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()
        
        threading.Thread(target=self.thingspeak_worker, daemon=True).start()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.client.loop_stop()
            self.client.disconnect()

if __name__ == "__main__":
    adaptor = MultiChannelThingSpeakAdaptor()
    adaptor.start()