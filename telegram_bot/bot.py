import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import paho.mqtt.client as mqtt
import json

class TelegramBotService:
    def __init__(self, token, broker, port):
        self.bot = telebot.TeleBot(token)
        self.broker = broker
        self.port = port
        
        self.sensor_data = {
            "temp_in": "--", "temp_out": "--",
            "hum_in": "--", "hum_out": "--",
            "light_in": "--", "light_out": "--"
        }
        self.active_chat_id = None
        
        # Setup MQTT
        self.mqtt_client = mqtt.Client(client_id="Telegram_Bot_PRO_Service_OOP")
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        
        # ثبت کردن (Binding) متدهای کلاس به عنوان هندلرهای تلگرام
        self.bot.register_message_handler(self.send_welcome, commands=['start', 'help'])
        self.bot.register_message_handler(self.handle_buttons, func=lambda message: True)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ PRO Telegram Bot connected to MQTT Broker!")
            self.mqtt_client.subscribe("home/actuator/window")
            self.mqtt_client.subscribe("home/sensor/+/+")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
            value = payload.get("value") if isinstance(payload, dict) else float(payload.decode())
            
            if "temp/indoor" in topic: self.sensor_data["temp_in"] = value
            elif "temp/outdoor" in topic: self.sensor_data["temp_out"] = value
            elif "hum/indoor" in topic: self.sensor_data["hum_in"] = value
            elif "hum/outdoor" in topic: self.sensor_data["hum_out"] = value
            elif "light/indoor" in topic: self.sensor_data["light_in"] = value
            elif "light/outdoor" in topic: self.sensor_data["light_out"] = value
            
            elif "actuator/window" in topic:
                command = payload.get("command", "UNKNOWN")
                reason = payload.get("reason", "No reason provided")
                if self.active_chat_id:
                    alert_text = f"🚨 **Smart Window Alert**\n\nState: {command} WINDOW\nReason: {reason}"
                    self.bot.send_message(self.active_chat_id, alert_text)
        except:
            pass

    def main_menu_keyboard(self):
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            KeyboardButton("🟢 Open Window"), KeyboardButton("🔴 Close Window"),
            KeyboardButton("🌡 Indoor Temp"), KeyboardButton("🌡 Outdoor Temp"),
            KeyboardButton("💧 Indoor Hum"), KeyboardButton("💧 Outdoor Hum"),
            KeyboardButton("☀️ Indoor Light"), KeyboardButton("☀️ Outdoor Light")
        )
        return markup

    def send_welcome(self, message):
        self.active_chat_id = message.chat.id
        welcome_text = "👋 Welcome to the PRO Smart Window Control Center!\n\nUse the buttons below to control the system or check real-time sensor data."
        self.bot.reply_to(message, welcome_text, reply_markup=self.main_menu_keyboard())

    def handle_buttons(self, message):
        text = message.text
        if text == "🟢 Open Window":
            self.mqtt_client.publish("home/actuator/window", json.dumps({"command": "OPEN", "reason": "Telegram App Manual Override"}))
            self.bot.reply_to(message, "✅ Command sent to system: OPEN")
        elif text == "🔴 Close Window":
            self.mqtt_client.publish("home/actuator/window", json.dumps({"command": "CLOSE", "reason": "Telegram App Manual Override"}))
            self.bot.reply_to(message, "✅ Command sent to system: CLOSE")
        elif text == "🌡 Indoor Temp":
            self.bot.reply_to(message, f"🌡 Current Indoor Temperature: {self.sensor_data['temp_in']} °C")
        elif text == "🌡 Outdoor Temp":
            self.bot.reply_to(message, f"🌲 Current Outdoor Temperature: {self.sensor_data['temp_out']} °C")
        elif text == "💧 Indoor Hum":
            self.bot.reply_to(message, f"💧 Current Indoor Humidity: {self.sensor_data['hum_in']} %")
        elif text == "💧 Outdoor Hum":
            self.bot.reply_to(message, f"🌲 Current Outdoor Humidity: {self.sensor_data['hum_out']} %")
        elif text == "☀️ Indoor Light":
            self.bot.reply_to(message, f"☀️ Current Indoor Light: {self.sensor_data['light_in']} lux")
        elif text == "☀️ Outdoor Light":
            self.bot.reply_to(message, f"🌲 Current Outdoor Light: {self.sensor_data['light_out']} lux")
        else:
            self.bot.reply_to(message, "I don't understand that command.", reply_markup=self.main_menu_keyboard())

    def start(self):
        self.mqtt_client.connect(self.broker, self.port, 60)
        self.mqtt_client.loop_start()
        print("🤖 PRO Telegram Bot (OOP) is running...")
        self.bot.infinity_polling()

if __name__ == "__main__":
    # توکن خودت رو دوباره اینجا بذار
    TOKEN = "8231580355:AAFnUTi1HemH7pM7AXRyuLNmc5Wo5COvmxo"
    bot_service = TelegramBotService(TOKEN, "broker.hivemq.com", 1883)
    bot_service.start()