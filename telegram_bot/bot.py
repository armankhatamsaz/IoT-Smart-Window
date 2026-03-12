import time
import json
import requests
import threading
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
        
        self.active_sessions = {}
        self.latest_sensor_data = {}
        self.window_states = {} # 🧠 حافظه جدید برای ذخیره وضعیت پنجره‌ها

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
                    self.users_db = config["users"]
                    print("✅ Registry Config received (Broker & Users info loaded)!")
                    break
            except Exception as e:
                time.sleep(3)

    # ================= MQTT Methods =================
    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ Bot Connected to MQTT Broker!")
            self.mqtt_client.subscribe("home/+/sensor/+")
            self.mqtt_client.subscribe("home/+/actuator/window") # سابسکرایب به وضعیت پنجره‌ها
        else:
            print(f"❌ MQTT Connection failed: {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            parts = msg.topic.split('/')
            
            if len(parts) >= 4:
                zone = parts[1]
                msg_type = parts[2] # sensor یا actuator
                
                if msg_type == 'sensor':
                    sensor_type = parts[3]
                    if zone not in self.latest_sensor_data:
                        self.latest_sensor_data[zone] = {}
                    self.latest_sensor_data[zone][sensor_type] = payload.get("value")
                    
                elif msg_type == 'actuator':
                    command = payload.get("command")
                    reason = payload.get("reason", "Auto/System")
                    
                    # ذخیره وضعیت جدیدِ پنجره تو حافظه ربات
                    self.window_states[zone] = command
                    
                    # 🚀 ارسال آلرت هوشمند فقط به کاربرانی که دسترسی دارن
                    for chat_id, user in self.active_sessions.items():
                        for room in user['allowed_rooms']:
                            if room.replace(" ", "").lower() == zone:
                                emoji = "🟢" if command == "OPEN" else "🔴"
                                self.bot.sendMessage(
                                    chat_id, 
                                    f"🔔 {emoji} **Auto-Alert**: [{room}] window was {command}ED.\n_(Triggered by: {reason})_",
                                    parse_mode='Markdown'
                                )
                                
        except json.JSONDecodeError:
            pass # نادیده گرفتن دیتاهای کثیف شبکه‌های عمومی
        except Exception as e:
            print(f"⚠️ [Bot] MQTT parse error: {e}")

    def start_mqtt(self):
        self.mqtt_client = mqtt.Client(client_id="TelegramBot_Client", transport=self.transport)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.connect(self.broker, self.port, 60)
        self.mqtt_client.loop_start()

    # ================= Telegram Methods =================
    def handle_message(self, msg):
        chat_id = msg['chat']['id']
        command = msg.get('text', '')

        if command == '/start':
            self.bot.sendMessage(chat_id, "🤖 Welcome to Smart Home Bot!\n🔒 Please login first.\nType: /login <username> <password>\n(Example: /login user1 1234)")
            return

        if command.startswith('/login'):
            parts = command.split()
            if len(parts) == 3:
                username = parts[1]
                password = parts[2]
                
                user = next((u for u in self.users_db if u['username'] == username and u['password'] == password), None)
                
                if user:
                    self.active_sessions[chat_id] = user
                    buttons = [[KeyboardButton(text=f"📊 Status: {room}")] for room in user['allowed_rooms']]
                    buttons += [[KeyboardButton(text=f"🪟 OPEN {room}"), KeyboardButton(text=f"🪟 CLOSE {room}")] for room in user['allowed_rooms'] if room != "Outdoor"]
                    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
                    
                    self.bot.sendMessage(chat_id, f"✅ Welcome back, {username}!\nYour authorized rooms are loaded.", reply_markup=keyboard)
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
            
            if room_name in user_info['allowed_rooms']:
                topic_zone = room_name.replace(" ", "").lower()
                actuator_topic = f"home/{topic_zone}/actuator/window"
                
                cmd_payload = json.dumps({"command": action, "reason": f"Telegram Manual ({user_info['username']})"})
                self.mqtt_client.publish(actuator_topic, cmd_payload)
                # پیام تاییدیه ارسال فرمان
                self.bot.sendMessage(chat_id, f"⏳ Command sent to {room_name}...")
            else:
                self.bot.sendMessage(chat_id, "⛔ You don't have permission for this room.")

        elif command.startswith("📊 Status:"):
            room_name = command.replace("📊 Status: ", "")
            if room_name in user_info['allowed_rooms']:
                topic_zone = room_name.replace(" ", "").lower()
                data = self.latest_sensor_data.get(topic_zone, {})
                win_state = self.window_states.get(topic_zone, "UNKNOWN (Wait for action)")
                
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
        self.discover_services()
        self.start_mqtt()
        threading.Thread(target=self.custom_polling_loop, daemon=True).start()
        print("🤖 Telegram Bot is listening (Smart Alerts Mode)...")
        
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

if __name__ == "__main__":
    smart_bot = SmartHomeBot()
    smart_bot.start()