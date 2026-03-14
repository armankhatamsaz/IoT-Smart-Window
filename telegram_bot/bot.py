import time
import json
import requests
import threading
import random
import string
import sys
import telepot
import paho.mqtt.client as mqtt
from telepot.namedtuple import ReplyKeyboardMarkup, KeyboardButton

class SmartHomeBot:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.users_db = []
        
        # شناسه یکتای سرویس
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        self.service_id = f"telegram_bot_{random_suffix}"
        
        self.active_sessions = {}
        self.latest_sensor_data = {}
        self.window_states = {}

        self.service_info = {
            "serviceID": self.service_id,
            "serviceName": "Telegram Control Bot",
            "availableServices": ["MQTT"],
            "servicesDetails": [
                {
                    "serviceType": "MQTT",
                    "topic": ["home/+/sensor/+", "home/+/actuator/window"]
                }
            ]
        }

        self.mqtt_client = None
        self.bot = telepot.Bot(self.bot_token)

    def load_settings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                self.bot_token = settings.get("bot_token")
        except FileNotFoundError:
            print("❌ ERROR: settings.json not found!")
            sys.exit(1)

    def discover_and_register(self):
        print(f"🔍 Contacting Registry at {self.registry_url}...")
        
        # 1. گرفتن کانفیگ (بروکر + یوزرها) از مسیر ترکیبی
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/config", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["broker"]["IP"]
                    self.port = config["broker"]["port"]
                    self.transport = config["broker"]["transport"]
                    self.users_db = config["usersList"]
                    print("✅ Registry Config received (Broker & Users info loaded)!")
                    break
            except Exception:
                time.sleep(3)

        # 2. ثبت نام در کاتالوگ
        try:
            resp = requests.post(f"{self.registry_url}/servicesList", json=self.service_info)
            if resp.status_code == 200:
                print("✅ Telegram Bot registered successfully in Catalog!")
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

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ Bot Connected to MQTT Broker!")
            self.mqtt_client.subscribe("home/+/sensor/+")
            self.mqtt_client.subscribe("home/+/actuator/window")
        else:
            print(f"❌ MQTT Connection failed: {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            parts = msg.topic.split('/')
            
            if len(parts) >= 4:
                zone = parts[1]
                msg_type = parts[2]
                
                if msg_type == 'sensor':
                    sensor_type = parts[3]
                    if zone not in self.latest_sensor_data:
                        self.latest_sensor_data[zone] = {}
                    self.latest_sensor_data[zone][sensor_type] = payload.get("value")
                    
                elif msg_type == 'actuator':
                    command = payload.get("command")
                    reason = payload.get("reason", "Auto/System")
                    self.window_states[zone] = command
                    
                    for chat_id, user in self.active_sessions.items():
                        # همگام‌سازی با نام‌های جدید در کاتالوگ استاد
                        for room in user.get('allowed_zones', []):
                            if room.replace(" ", "").lower() == zone:
                                emoji = "🟢" if command == "OPEN" else "🔴"
                                self.bot.sendMessage(
                                    chat_id, 
                                    f"🔔 {emoji} **Auto-Alert**: [{room}] window was {command}ED.\n_(Triggered by: {reason})_",
                                    parse_mode='Markdown'
                                )
                                
        except json.JSONDecodeError:
            pass 
        except Exception as e:
            print(f"⚠️ [Bot] MQTT parse error: {e}")

    def start_mqtt(self):
        self.mqtt_client = mqtt.Client(client_id=f"MQTT_{self.service_id}", transport=self.transport)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.connect(self.broker, self.port, 60)
        self.mqtt_client.loop_start()

    def handle_message(self, msg):
        chat_id = msg['chat']['id']
        command = msg.get('text', '')

        if command == '/start':
            self.bot.sendMessage(chat_id, "🤖 Welcome to Smart Home Bot!\n🔒 Please login first.\nType: /login <username> <password>")
            return

        if command.startswith('/login'):
            parts = command.split()
            if len(parts) == 3:
                username = parts[1]
                password = parts[2]
                
                # استفاده از userName طبق کاتالوگ جدید
                user = next((u for u in self.users_db if u.get('userName') == username and u.get('password') == password), None)
                
                if user:
                    self.active_sessions[chat_id] = user
                    buttons = [[KeyboardButton(text=f"📊 Status: {room}")] for room in user.get('allowed_zones', [])]
                    buttons += [[KeyboardButton(text=f"🪟 OPEN {room}"), KeyboardButton(text=f"🪟 CLOSE {room}")] for room in user.get('allowed_zones', []) if room != "Outdoor"]
                    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
                    
                    self.bot.sendMessage(chat_id, f"✅ Welcome back, {username}!\nYour authorized zones are loaded.", reply_markup=keyboard)
                else:
                    self.bot.sendMessage(chat_id, "❌ Invalid username or password.")
            return

        if chat_id not in self.active_sessions:
            self.bot.sendMessage(chat_id, "🔒 Please login first.\nType: /login <username> <password>")
            return

        user_info = self.active_sessions[chat_id]

        if command.startswith("🪟 OPEN") or command.startswith("🪟 CLOSE"):
            action = "OPEN" if "OPEN" in command else "CLOSE"
            room_name = command.replace(f"🪟 {action} ", "")
            
            if room_name in user_info.get('allowed_zones', []):
                topic_zone = room_name.replace(" ", "").lower()
                actuator_topic = f"home/{topic_zone}/actuator/window"
                
                cmd_payload = json.dumps({"command": action, "reason": f"Telegram Manual ({user_info.get('userName')})"})
                self.mqtt_client.publish(actuator_topic, cmd_payload)
                self.bot.sendMessage(chat_id, f"⏳ Command sent to {room_name}...")
            else:
                self.bot.sendMessage(chat_id, "⛔ You don't have permission for this zone.")

        elif command.startswith("📊 Status:"):
            room_name = command.replace("📊 Status: ", "")
            if room_name in user_info.get('allowed_zones', []):
                topic_zone = room_name.replace(" ", "").lower()
                data = self.latest_sensor_data.get(topic_zone, {})
                win_state = self.window_states.get(topic_zone, "UNKNOWN")
                
                if data:
                    text = f"🏢 **{room_name} Overview**\n"
                    text += f"🪟 Window State: **{win_state}**\n\n"
                    text += f"🌡️ Temperature: {data.get('temp', '--')} °C\n"
                    text += f"💧 Humidity: {data.get('hum', '--')} %\n"
                    text += f"☀️ Light: {data.get('light', '--')} lux"
                    self.bot.sendMessage(chat_id, text, parse_mode='Markdown')
                else:
                    self.bot.sendMessage(chat_id, f"⏳ Waiting for sensor data from {room_name}...")

    def custom_polling_loop(self):
        offset = None
        while True:
            try:
                updates = self.bot.getUpdates(offset=offset)
                for update in updates:
                    offset = update['update_id'] + 1
                    if 'message' in update:
                        self.handle_message(update['message'])
            except Exception:
                pass
            time.sleep(1)

    def start(self):
        self.discover_and_register()
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()
        self.start_mqtt()
        threading.Thread(target=self.custom_polling_loop, daemon=True).start()
        print("🤖 Telegram Bot is listening (Compliant Mode)...")
        
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

if __name__ == "__main__":
    smart_bot = SmartHomeBot()
    smart_bot.start()