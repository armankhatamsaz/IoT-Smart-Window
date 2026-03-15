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

class SmartAnalyzer:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.client = None
        
        # شناسه یکتا طبق استاندارد استاد
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        self.service_id = f"analyzer_{random_suffix}"

        # حافظه تاریخی برای پیاده‌سازی الگوریتم‌های پیچیده (Moving Average)
        # فرمت: {"livingroom": [25.5, 26.0, 26.1], "outdoor": [15.0, 15.2]}
        self.temp_history = {}
        self.HISTORY_LIMIT = 5 # نگهداری 5 دیتای آخر برای میانگین‌گیری

        self.last_command = {}
        # پروفایل سرویس برای ثبت در کاتالوگ
        self.service_info = {
            "serviceID": self.service_id,
            "serviceName": self.service_name,
            "availableServices": ["MQTT"],
            "servicesDetails": [
                {
                    "serviceType": "MQTT",
                    "topic": ["home/+/sensor/temp", "home/+/actuator/window"]
                }
            ]
        }

    def load_settings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.service_name = settings.get("service_name", "Analyzer")
        except FileNotFoundError:
            print("❌ FATAL ERROR: Analyzer settings.json not found!")
            sys.exit(1)

    def discover_and_register(self):
        print(f"🔍 Contacting Registry at {self.registry_url}...")
        
        # 1. گرفتن اطلاعات بروکر
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/broker", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["IP"]
                    self.port = config["port"]
                    self.transport = config["transport"]
                    print(f"✅ Broker config received: {self.broker}:{self.port}")
                    break
            except Exception:
                time.sleep(3)

        # 2. ثبت نام در کاتالوگ (مسیر جدید servicesList)
        try:
            resp = requests.post(f"{self.registry_url}/servicesList", json=self.service_info)
            if resp.status_code == 200:
                print("✅ Analyzer registered successfully in Catalog!")
        except Exception as e:
            print(f"❌ Registration failed: {e}")

    def heartbeat_worker(self):
        """تپش قلب هر 50 ثانیه برای زنده ماندن در کاتالوگ"""
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
            print("✅ Analyzer Connected to MQTT Broker!")
            # سابسکرایب شدن به تمام سنسورهای دما برای مقایسه
            self.client.subscribe("home/+/sensor/temp")
        else:
            print(f"❌ Connection failed: {rc}")

    def evaluate_smart_logic(self, zone):
        """
        الگوریتم پیچیده (تایید شده توسط استاد رافائل):
        - استفاده از میانگین متحرک (Moving Average)
        - مقایسه دمای داخل با دمای محیط بیرون (Correlation)
        """
        if zone == "outdoor":
            return # بیرون که پنجره نداره!

        indoor_data = self.temp_history.get(zone, [])
        outdoor_data = self.temp_history.get("outdoor", [])

        # تا زمانی که حداقل 3 تا دیتا برای میانگین‌گیری جمع نشده، تصمیمی نگیر (جلوگیری از نویز)
        if len(indoor_data) < 3:
            return

        indoor_avg = round(sum(indoor_data) / len(indoor_data), 2)
        
        # اگه دیتای بیرون هنوز نرسیده، نمیتونیم تصمیم هوشمند بگیریم
        if len(outdoor_data) < 1:
            return
            
        outdoor_avg = round(sum(outdoor_data) / len(outdoor_data), 2)

        print(f"🧠 [AI Logic] {zone.upper()} | In_Avg: {indoor_avg}°C | Out_Avg: {outdoor_avg}°C")

        actuator_topic = f"home/{zone}/actuator/window"
        command = None
        reason = ""

        # --- سناریو 1: داخل خونه خیلی گرم شده (بالای 26 درجه) ---
        if indoor_avg > 26.0:
            if outdoor_avg < indoor_avg - 1.0:
                # بیرون خنک‌تره -> پنجره رو باز کن تا هوای خنک بیاد تو (Free Cooling)
                command = "OPEN"
                reason = f"Hot inside ({indoor_avg}°C) but cooler outside ({outdoor_avg}°C). Free cooling enabled."
            else:
                # بیرون از داخل هم گرم‌تره! -> پنجره رو ببند تا جهنم نشه!
                command = "CLOSE"
                reason = f"Hot inside ({indoor_avg}°C) and extremely hot outside ({outdoor_avg}°C). Blocking heat."

        # --- سناریو 2: داخل خونه سرد شده (زیر 22 درجه) ---
        elif indoor_avg < 22.0:
            if outdoor_avg > indoor_avg + 1.0:
                # بیرون گرم‌تره (مثلا ظهر زمستونه و آفتاب زده) -> پنجره رو باز کن خونه گرم شه
                command = "OPEN"
                reason = f"Cold inside ({indoor_avg}°C) but sunny/warm outside ({outdoor_avg}°C). Free heating."
            else:
                # بیرون هم یخبندانه -> پنجره رو ببند سوز نیاد!
                command = "CLOSE"
                reason = f"Cold inside ({indoor_avg}°C) and freezing outside ({outdoor_avg}°C). Preserving heat."

        # --- سناریو 3: دمای متعادل (22 تا 26) ---
        else:
            # تو دمای نرمال کاری نمی‌کنیم تا کاربر خودش بتونه دستی کنترل کنه
            pass

        # ارسال فرمان به MQTT
        # ارسال فرمان به MQTT (با بررسی حافظه وضعیت)
        if command:
            # فقط در صورتی پیام رو بفرست که فرمانِ جدید، با فرمانِ قبلی فرق داشته باشه!
            if command != self.last_command.get(zone):
                cmd_payload = json.dumps({"command": command, "reason": reason})
                self.client.publish(actuator_topic, cmd_payload)
                print(f"   ⚙️ ACTION: Sent {command} to {zone}. Reason: {reason}")
                
                # ذخیره فرمان جدید تو حافظه تا دفعه بعد تکراری نفرسته
                self.last_command[zone] = command
            else:
                # سیستم همون تصمیم رو گرفته، پنجره همون وضعیته، پس سکوت می‌کنیم (بدون اسپم)
                pass


    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            temp_value = payload.get("value")
            
            zone = msg.topic.split('/')[1]
            
            # ذخیره دیتا در حافظه تاریخی
            if zone not in self.temp_history:
                self.temp_history[zone] = []
                
            self.temp_history[zone].append(temp_value)
            
            # نگه داشتن فقط 5 دیتای آخر (Sliding Window)
            if len(self.temp_history[zone]) > self.HISTORY_LIMIT:
                self.temp_history[zone].pop(0)

            # فراخوانی هوش مصنوعی برای تصمیم‌گیری
            self.evaluate_smart_logic(zone)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"⚠️ [Analyzer] Error processing message: {e}")

    def start(self):
        self.discover_and_register()
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()

        self.client = mqtt.Client(client_id=f"MQTT_{self.service_id}", transport=self.transport)
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
    analyzer = SmartAnalyzer()
    analyzer.start()