import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import paho.mqtt.client as mqtt
import json

TOKEN = "8231580355:AAFnUTi1HemH7pM7AXRyuLNmc5Wo5COvmxo"
bot = telebot.TeleBot(TOKEN)

BROKER_ADDRESS = "broker.hivemq.com"
PORT = 1883

# حافظه ربات برای ذخیره آخرین مقادیر سنسورها
sensor_data = {
    "temp_in": "--", "temp_out": "--",
    "hum_in": "--", "hum_out": "--",
    "light_in": "--", "light_out": "--"
}

# متغیری برای ذخیره آیدی چت شما تا هشدارها رو مستقیماً برات بفرسته
active_chat_id = None

# --- بخش MQTT ---
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ PRO Telegram Bot connected to MQTT Broker!")
        # ربات حالا هم به اکچویتور گوش میده، هم به همه سنسورها
        client.subscribe("home/actuator/window")
        client.subscribe("home/sensor/+/+")

def on_message(client, userdata, msg):
    global active_chat_id
    topic = msg.topic
    
    try:
        payload = json.loads(msg.payload.decode())
        value = payload.get("value") if isinstance(payload, dict) else float(payload.decode())
        
        # ذخیره آخرین مقادیر در حافظه ربات
        if "temp/indoor" in topic: sensor_data["temp_in"] = value
        elif "temp/outdoor" in topic: sensor_data["temp_out"] = value
        elif "hum/indoor" in topic: sensor_data["hum_in"] = value
        elif "hum/outdoor" in topic: sensor_data["hum_out"] = value
        elif "light/indoor" in topic: sensor_data["light_in"] = value
        elif "light/outdoor" in topic: sensor_data["light_out"] = value
        
        # اگر فرمانی به پنجره ارسال شد، به شما هشدار بدهد
        elif "actuator/window" in topic:
            command = payload.get("command", "UNKNOWN")
            reason = payload.get("reason", "No reason provided")
            
            # اگر کاربر حداقل یک بار ربات را استارت کرده باشد، پیام می‌رود
            if active_chat_id:
                alert_text = f"🚨 **Smart Window Alert**\n\nState: {command} WINDOW\nReason: {reason}"
                bot.send_message(active_chat_id, alert_text)
                
    except Exception as e:
        pass

mqtt_client = mqtt.Client(client_id="Telegram_Bot_PRO_Service")
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(BROKER_ADDRESS, PORT, 60)
mqtt_client.loop_start()

# --- بخش تلگرام (رابط کاربری حرفه‌ای) ---

# تابع ساخت کیبورد دکمه‌ای تلگرام
def main_menu_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🟢 Open Window"), KeyboardButton("🔴 Close Window"),
        KeyboardButton("🌡 Indoor Temp"), KeyboardButton("🌡 Outdoor Temp"),
        KeyboardButton("💧 Indoor Hum"), KeyboardButton("💧 Outdoor Hum"),
        KeyboardButton("☀️ Indoor Light"), KeyboardButton("☀️ Outdoor Light")
    )
    return markup

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    global active_chat_id
    active_chat_id = message.chat.id  # ذخیره آیدی کاربر برای ارسال هشدارهای خودکار سیستم
    
    welcome_text = "👋 Welcome to the PRO Smart Window Control Center!\n\nUse the buttons below to control the system or check real-time sensor data."
    bot.reply_to(message, welcome_text, reply_markup=main_menu_keyboard())

# تابع پردازش کلیک روی دکمه‌ها
@bot.message_handler(func=lambda message: True)
def handle_buttons(message):
    text = message.text
    
    if text == "🟢 Open Window":
        mqtt_client.publish("home/actuator/window", json.dumps({"command": "OPEN", "reason": "Telegram App Manual Override"}))
        bot.reply_to(message, "✅ Command sent to system: OPEN")
        
    elif text == "🔴 Close Window":
        mqtt_client.publish("home/actuator/window", json.dumps({"command": "CLOSE", "reason": "Telegram App Manual Override"}))
        bot.reply_to(message, "✅ Command sent to system: CLOSE")
        
    elif text == "🌡 Indoor Temp":
        bot.reply_to(message, f"🌡 Current Indoor Temperature: {sensor_data['temp_in']} °C")
        
    elif text == "🌡 Outdoor Temp":
        bot.reply_to(message, f"🌲 Current Outdoor Temperature: {sensor_data['temp_out']} °C")
        
    elif text == "💧 Indoor Hum":
        bot.reply_to(message, f"💧 Current Indoor Humidity: {sensor_data['hum_in']} %")
        
    elif text == "💧 Outdoor Hum":
        bot.reply_to(message, f"🌲 Current Outdoor Humidity: {sensor_data['hum_out']} %")
        
    elif text == "☀️ Indoor Light":
        bot.reply_to(message, f"☀️ Current Indoor Light: {sensor_data['light_in']} lux")
        
    elif text == "☀️ Outdoor Light":
        bot.reply_to(message, f"🌲 Current Outdoor Light: {sensor_data['light_out']} lux")
        
    else:
        bot.reply_to(message, "I don't understand that command. Please use the menu buttons.", reply_markup=main_menu_keyboard())

print("🤖 PRO Telegram Bot is running...")
bot.infinity_polling()