import time
import json
import requests
import threading
import telepot
from telepot.loop import MessageLoop
from telepot.namedtuple import ReplyKeyboardMarkup, KeyboardButton
import paho.mqtt.client as mqtt

class SmartHomeBot:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.users_db = []
        
        # دیکشنری برای ذخیره کاربرهای لاگین شده تو تلگرام (chat_id: user_info)
        self.active_sessions = {}
        
        # دیکشنری برای کش کردن آخرین دیتای سنسورها
        self.latest_sensor_data = {}

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
                print("⏳ Waiting for Registry...")
                time.sleep(3)

    # ================= MQTT Methods =================
    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ Bot Connected to MQTT Broker!")
            # سابسکرایب به همه سنسورها برای نمایش وضعیت به کاربر
            self.mqtt_client.subscribe("home/+/sensor/+")
        else:
            print(f"❌ MQTT Connection failed: {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        parts = msg.topic.split('/')
        if len(parts) >= 4:
            zone = parts[1]
            sensor_type = parts[3]
            
            # مقاوم‌سازی در برابر دیتای غیر JSON تو بروکرهای عمومی
            try:
                payload = json.loads(msg.payload.decode())
                
                if zone not in self.latest_sensor_data:
                    self.latest_sensor_data[zone] = {}
                self.latest_sensor_data[zone][sensor_type] = payload.get("value")
            except json.JSONDecodeError:
                print(f"⚠️ [Bot] Ignored malformed JSON on {msg.topic}: {msg.payload.decode()}")

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

        print(f"📩 Message from {chat_id}: {command}")

        # دستور ورود کاربر
        if command.startswith('/login'):
            parts = command.split()
            if len(parts) == 3:
                username = parts[1]
                password = parts[2]
                
                # جستجو در دیتابیسی که از کاتالوگ خوندیم
                user = next((u for u in self.users_db if u['username'] == username and u['password'] == password), None)
                
                if user:
                    self.active_sessions[chat_id] = user
                    
                    # ساخت دکمه‌های اختصاصی برای این کاربر
                    buttons = [[KeyboardButton(text=f"📊 Status: {room}")] for room in user['allowed_rooms']]
                    buttons += [[KeyboardButton(text=f"🪟 OPEN {room}"), KeyboardButton(text=f"🪟 CLOSE {room}")] for room in user['allowed_rooms'] if room != "Outdoor"]
                    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
                    
                    self.bot.sendMessage(chat_id, f"✅ Welcome back, {username}!\nYour authorized rooms are loaded.", reply_markup=keyboard)
                else:
                    self.bot.sendMessage(chat_id, "❌ Invalid username or password.")
            else:
                self.bot.sendMessage(chat_id, "⚠️ Usage: /login <username> <password>")
            return

        # اگر کاربر لاگین نکرده بود
        if chat_id not in self.active_sessions:
            self.bot.sendMessage(chat_id, "🔒 Please login first.\nType: /login <username> <password>\n(Example: /login user1 1234)")
            return

        user_info = self.active_sessions[chat_id]

        # هندل کردن دکمه‌های کنترل پنجره
        if command.startswith("🪟 OPEN") or command.startswith("🪟 CLOSE"):
            action = "OPEN" if "OPEN" in command else "CLOSE"
            room_name = command.replace(f"🪟 {action} ", "")
            
            if room_name in user_info['allowed_rooms']:
                topic_zone = room_name.replace(" ", "").lower()
                actuator_topic = f"home/{topic_zone}/actuator/window"
                
                cmd_payload = json.dumps({"command": action, "reason": f"Manual command from Telegram ({user_info['username']})"})
                self.mqtt_client.publish(actuator_topic, cmd_payload)
                self.bot.sendMessage(chat_id, f"✅ Command '{action}' sent to {room_name} window.")
            else:
                self.bot.sendMessage(chat_id, "⛔ You don't have permission for this room.")

        # هندل کردن دکمه وضعیت
        elif command.startswith("📊 Status:"):
            room_name = command.replace("📊 Status: ", "")
            if room_name in user_info['allowed_rooms']:
                topic_zone = room_name.replace(" ", "").lower()
                data = self.latest_sensor_data.get(topic_zone, {})
                
                if data:
                    text = f"🌡️ **{room_name} Status**\n"
                    text += f"Temperature: {data.get('temp', 'N/A')} °C\n"
                    text += f"Humidity: {data.get('hum', 'N/A')} %\n"
                    text += f"Light: {data.get('light', 'N/A')} lux"
                    self.bot.sendMessage(chat_id, text, parse_mode='Markdown')
                else:
                    self.bot.sendMessage(chat_id, f"⏳ Waiting for sensor data from {room_name}...")
            else:
                self.bot.sendMessage(chat_id, "⛔ You don't have permission for this room.")

    def start(self):
        # 1. رجیستر شدن و گرفتن اطلاعات شبکه
        self.discover_services()
        
        # 2. استارت کردن MQTT تو بک‌گراند
        self.start_mqtt()
        
        # 3. استارت کردن ربات تلگرام
        MessageLoop(self.bot, self.handle_message).run_as_thread()
        print("🤖 Telegram Bot is listening...")
        
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            print("\n🛑 Bot stopped.")
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

if __name__ == "__main__":
    smart_bot = SmartHomeBot()
    smart_bot.start()