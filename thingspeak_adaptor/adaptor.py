import time
import json
import requests
import warnings
import threading
warnings.filterwarnings("ignore", category=DeprecationWarning)

import paho.mqtt.client as mqtt

class MultiChannelThingSpeakAdaptor:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        
        # دیکشنری پیشرفته برای ذخیره دیتای هر کانال به صورت مجزا بر اساس API_KEY
        self.buffers = {channel["api_key"]: {} for channel in self.channels}

    def load_settings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.channels = settings.get("channels", [])
                self.service_info = {
                    "service_name": settings.get("service_name", "TS_Adaptor_Multi"),
                    "type": "Data Logger"
                }
        except FileNotFoundError:
            print("❌ ERROR: settings.json not found for Adaptor!")
            exit(1)

    def discover_services(self):
        print(f"🔍 Contacting Registry at {self.registry_url}...")
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/config", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["broker"]["host"]
                    self.port = config["broker"]["port"]
                    self.transport = config["broker"]["transport"]
                    print("✅ Registry Config received!")
                    break
            except Exception as e:
                print("⏳ Waiting for Registry...")
                time.sleep(3)
                
        try:
            requests.post(f"{self.registry_url}/services", json=self.service_info)
            print("✅ Adaptor registered in Catalog!")
        except:
            pass

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ Adaptor Connected to MQTT Broker!")
            # سابسکرایب شدن به تمامی تاپیک‌های موجود در تمامی کانال‌ها
            for channel in self.channels:
                for topic in channel["mapping"].keys():
                    client.subscribe(topic)
                    print(f"📡 Subscribed for TS: {topic}")
        else:
            print(f"❌ Connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = json.loads(msg.payload.decode())
        
        # پیدا کردن اینکه این تاپیک مال کدوم کانال و کدوم فیلده
        for channel in self.channels:
            if topic in channel["mapping"]:
                field_name = channel["mapping"][topic]
                api_key = channel["api_key"]
                # ذخیره در بافر مخصوص همون کانال
                self.buffers[api_key][field_name] = payload.get("value")

    def thingspeak_worker(self):
        """پردازشگر بک‌گراند برای ارسال دیتا به صورت دسته‌ای"""
        url = "https://api.thingspeak.com/update"
        
        while True:
            time.sleep(20) # دور زدن محدودیت زمانی تینگ‌اسپیک
            
            # ارسال دیتای هر کانال به صورت جداگانه
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
                            print(f"☁️ [{channel_name}] Uploaded successfully! {buffer_data}")
                        else:
                            print(f"⚠️ [{channel_name}] Upload failed. HTTP: {response.status_code}")
                    except Exception as e:
                        print(f"❌ [{channel_name}] Connection Error: {e}")
                    
                    # پاک کردن بافرِ همون کانال بعد از ارسال
                    self.buffers[api_key].clear()

    def start(self):
        self.discover_services()

        self.client = mqtt.Client(client_id="ThingSpeak_Multi_Adaptor", transport=self.transport)
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
            print("\n🛑 Adaptor stopped.")
            self.client.loop_stop()
            self.client.disconnect()

if __name__ == "__main__":
    adaptor = MultiChannelThingSpeakAdaptor()
    adaptor.start()