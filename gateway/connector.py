import time
import random
import json
import warnings
import requests
import sys

warnings.filterwarnings("ignore", category=DeprecationWarning) 
import paho.mqtt.client as mqtt

class GenericSmartGateway:
    def __init__(self, settings_file='settings.json'):
        # ۱. خوندن هویتِ خودش از فایل کانفیگ (بدون هیچ هاردکدی)
        self.load_settings(settings_file)
        
        self.broker = None
        self.port = None
        self.transport = None
        self.client = None

        # ساخت ID و تاپیک استاندارد بر اساس اسم منطقه
        safe_zone_name = self.zone_name.replace(" ", "_").lower()
        self.device_id = f"gateway_{safe_zone_name}"
        self.topic_base = self.zone_name.replace(" ", "").lower()

        # ساخت پروفایل برای معرفی به رجیستری
        self.device_info = {
            "device_id": self.device_id,
            "type": "Gateway",
            "location": self.zone_name,
            "sensors": self.sensors,
            "actuator": "window" if self.has_actuator else "none"
        }

    def load_settings(self, filepath):
        """خوندن هویت و قابلیت‌ها از فایل JSON"""
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.zone_name = settings.get("zone_name", "Unknown Zone")
                self.sensors = settings.get("sensors", [])
                self.has_actuator = settings.get("has_actuator", False)
        except FileNotFoundError:
            print(f"❌ FATAL ERROR: Configuration file '{filepath}' not found!")
            print("A commercial IoT device cannot start without its provisioning identity.")
            sys.exit(1) # تو صنعت، اگه کانفیگ نباشه دستگاه بالا نمیاد!

        # تلاش برای پیدا کردن رجیستری (داکر یا لوکال)
        try:
            requests.get(self.registry_url, timeout=1)
        except requests.exceptions.RequestException:
            self.registry_url = "http://127.0.0.1:8080"

    def discover_and_register(self):
        """گرفتن تنظیمات شبکه و ثبت خودش تو کاتالوگ مرکزی"""
        print(f"🔍 [{self.zone_name}] Contacting Registry at {self.registry_url}...")
        
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/config", timeout=5)
                if resp.status_code == 200:
                    config_data = resp.json()
                    self.broker = config_data["broker"]["host"]
                    self.port = config_data["broker"]["port"]
                    self.transport = config_data["broker"]["transport"]
                    print(f"✅ [{self.zone_name}] Broker config received: {self.broker}:{self.port}")
                    break
            except Exception as e:
                print(f"⏳ [{self.zone_name}] Waiting for Registry to come online...")
                time.sleep(3)

        try:
            resp = requests.post(f"{self.registry_url}/devices", json=self.device_info)
            if resp.status_code == 201:
                print(f"✅ [{self.zone_name}] Device registered successfully in Catalog!")
        except Exception as e:
            print(f"❌ [{self.zone_name}] Registration failed: {e}")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✅ [{self.zone_name}] Connected to MQTT Broker!")
            
            # فقط اگه تو فایل کانفیگ نوشته بود اکچویتور داره، سابسکرایب میکنه!
            if self.has_actuator:
                topic = f"home/{self.topic_base}/actuator/window"
                self.client.subscribe(topic)
                print(f"📡 [{self.zone_name}] Subscribed to Actuator: {topic}")
            else:
                print(f"📡 [{self.zone_name}] No Actuators configured. Running in Sensor-Only mode.")
        else:
            print(f"❌ Connection failed with code {rc}")

    def on_message(self, client, userdata, msg):
        payload = json.loads(msg.payload.decode())
        command = payload.get("command")
        
        if command == "OPEN":
            print(f"\n🪟 🟢 [{self.zone_name}] RELAY TRIGGERED: Opening Window!\n")
        elif command == "CLOSE":
            print(f"\n🪟 🔴 [{self.zone_name}] RELAY TRIGGERED: Closing Window!\n")

    def start(self):
        self.discover_and_register()

        self.client = mqtt.Client(client_id=f"Client_{self.device_id}", transport=self.transport)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        print(f"⏳ [{self.zone_name}] Connecting to Broker...")
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()
        
        try:
            while True:
                # داینامیک دیتای سنسور تولید میکنه (فقط برای سنسورهایی که تو کانفیگ تعریف شدن)
                published_data = []
                
                if "temp" in self.sensors:
                    val = round(random.uniform(10.0, 35.0) if self.zone_name == "Outdoor" else random.uniform(20.0, 28.0), 2)
                    self.client.publish(f"home/{self.topic_base}/sensor/temp", json.dumps({"value": val, "unit": "C"}))
                    published_data.append(f"Temp: {val}°C")
                    
                if "hum" in self.sensors:
                    val = round(random.uniform(20.0, 80.0) if self.zone_name == "Outdoor" else round(random.uniform(30.0, 60.0), 2), 2)
                    self.client.publish(f"home/{self.topic_base}/sensor/hum", json.dumps({"value": val, "unit": "%"}))
                    published_data.append(f"Hum: {val}%")
                    
                if "light" in self.sensors:
                    val = round(random.uniform(100, 5000) if self.zone_name == "Outdoor" else round(random.uniform(200, 800), 2), 2)
                    self.client.publish(f"home/{self.topic_base}/sensor/light", json.dumps({"value": val, "unit": "lux"}))
                    published_data.append(f"Light: {val}lx")
                
                if published_data:
                    print(f"📤 [{self.zone_name}] Published -> " + " | ".join(published_data))
                
                time.sleep(5)
                
        except KeyboardInterrupt:
            print(f"\n🛑 [{self.zone_name}] Gateway stopped.")
            self.client.loop_stop()
            self.client.disconnect()

if __name__ == "__main__":
    import os
    # داکر به هر کانتینر میگه که کدوم فایل رو بخونه، اگه نگفت پیش‌فرض میشه پذیرایی
    config_file = os.environ.get("CONFIG_FILE", "settings_livingroom.json")
    
    gateway = GenericSmartGateway(config_file)
    gateway.start()