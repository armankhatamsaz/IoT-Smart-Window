import telebot
import paho.mqtt.client as mqtt
import json

# Place your Telegram Bot Token here
TOKEN = "8231580355:AAFnUTi1HemH7pM7AXRyuLNmc5Wo5COvmxo"
bot = telebot.TeleBot(TOKEN)

BROKER_ADDRESS = "broker.hivemq.com"
PORT = 1883

# --- MQTT Section (For receiving alerts from the system) ---
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ Telegram Bot connected to MQTT Broker!")
        # Subscribe to the actuator topic to monitor window state changes
        client.subscribe("home/actuator/window")

def on_message(client, userdata, msg):
    # When a message arrives on the MQTT network, the bot processes it
    if msg.topic == "home/actuator/window":
        payload = json.loads(msg.payload.decode())
        command = payload.get("command")
        reason = payload.get("reason")
        
        alert_text = f"🚨 **Smart Window System**\n\nNew State: {command}\nReason: {reason}"
        
        # Note: In a real-world scenario, you would send this to the user's Chat ID.
        # For testing, we are logging it in the terminal.
        print(f"🔔 Notification ready to send: {command} - {reason}")

mqtt_client = mqtt.Client(client_id="Telegram_Bot_Service")
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(BROKER_ADDRESS, PORT, 60)
mqtt_client.loop_start() # Run in the background

# --- Telegram Section (For receiving manual commands from the user) ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "👋 Welcome to the Smart Window Control Bot!\n\n"
        "Try the following commands:\n"
        "🟢 /open - Manually open the window\n"
        "🔴 /close - Manually close the window"
    )
    bot.reply_to(message, welcome_text)

@bot.message_handler(commands=['open'])
def open_window(message):
    # Send OPEN command to the MQTT network
    payload = json.dumps({"command": "OPEN", "reason": "Manual override via Telegram"})
    mqtt_client.publish("home/actuator/window", payload)
    bot.reply_to(message, "✅ OPEN command sent to the system successfully.")

@bot.message_handler(commands=['close'])
def close_window(message):
    # Send CLOSE command to the MQTT network
    payload = json.dumps({"command": "CLOSE", "reason": "Manual override via Telegram"})
    mqtt_client.publish("home/actuator/window", payload)
    bot.reply_to(message, "✅ CLOSE command sent to the system successfully.")

print("🤖 Telegram Bot is running and listening for commands...")
bot.infinity_polling()