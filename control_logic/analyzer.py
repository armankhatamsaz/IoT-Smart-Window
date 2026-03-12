import time
import json
import requests
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import paho.mqtt.client as mqtt

class ControlLogic:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.client = None
        
        # آستانه دما برای باز شدن پنجره
        self.TEMP_THRESHOLD = 27.0 

    def load_settings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.service_info = {
                    "service_name": settings.get("service_name", "Analyzer"),
                    "description": settings.get("description", ""),
                    "type": "Control Logic"
                }
        except FileNotFoundError:
            self.registry_url = "http://127.0.0.1:8080"
            self.service_info = {"service_name": "Analyzer", "type": "Control Logic"}

    def discover_and_register(self):
        print(f"🔍 Contacting Registry at {self.registry_url}...")
        
        # ۱. گرفتن اطلاعات شبکه از کاتالوگ
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/config", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["broker"]["host"]
                    self.port = config["broker"]["port"]
                    self.transport = config["broker"]["transport"]
                    print(f"✅ Broker config received: {self.broker}:{self.port}")
                    break
            except Exception as e:
                print("⏳ Waiting for Registry...")
                time.sleep(3)

        # ۲. ثبت خودش به عنوان "سرویس" تو کاتالوگ
        try:
            resp = requests.post(f"{self.registry_url}/services", json=self.service_info)
            if resp.status_code == 201:
                print("✅ Control Logic registered successfully in Catalog!")
        except Exception as e:
            print(f"❌ Registration failed: {e}")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ Analyzer Connected to MQTT Broker!")
            
            # جادوی MQTT: با گذاشتن '+' میگیم دیتای سنسور دمای "همه" اتاق‌ها رو به من بده!
            # نیازی نیست اسم اتاق‌ها رو اینجا بنویسیم.
            topic = "home/+/sensor/temp"
            self.client.subscribe(topic)
            print(f"📡 Subscribed to All Temperature Sensors: {topic}")
        else:
            print(f"❌ Connection failed with code {rc}")

    def on_message(self, client, userdata, msg):
        payload = json.loads(msg.payload.decode())
        temp_value = payload.get("value")
        
        # استخراج اسم اتاق از روی تاپیک (مثلاً از home/livingroom/sensor/temp کلمه livingroom رو درمیاره)
        zone = msg.topic.split('/')[1]
        
        # محیط بیرون (outdoor) رو آنالیز نمی‌کنیم چون پنجره‌ای برای باز کردن نداره!
        if zone == "outdoor":
            return

        print(f"🧠 [Analyzer] {zone.upper()} Temp is {temp_value}°C")
        
        # منطق کنترل
        actuator_topic = f"home/{zone}/actuator/window"
        
        if temp_value > self.TEMP_THRESHOLD:
            print(f"   ⚠️ Hot in {zone}! Sending OPEN command to window.")
            cmd = json.dumps({"command": "OPEN", "reason": f"Temp is {temp_value} > {self.TEMP_THRESHOLD}"})
            self.client.publish(actuator_topic, cmd)
        else:
            # اگه دوست داشتی میتونی منطق CLOSE رو هم اینجا بذاری
            pass

    def start(self):
        self.discover_and_register()

        self.client = mqtt.Client(client_id="SmartHome_Analyzer", transport=self.transport)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        print("⏳ Connecting to Broker...")
        self.client.connect(self.broker, self.port, 60)
        
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            print("\n🛑 Analyzer stopped.")
            self.client.disconnect()

if __name__ == "__main__":
    analyzer = ControlLogic()
    analyzer.start()