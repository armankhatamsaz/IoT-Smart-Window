import time
import random
import json
import paho.mqtt.client as mqtt

BROKER_ADDRESS = "broker.hivemq.com"
PORT = 1883

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ Gateway Connected to MQTT Broker Successfully!")
        # رسپری پای به تاپیک اکچویتور (پنجره) گوش میده تا دستورات رو دریافت کنه
        client.subscribe("home/actuator/window")
    else:
        print(f"❌ Failed to connect, return code {rc}")

# این تابع وقتی اجرا میشه که دستوری از سمت مغز متفکر بیاد
def on_message(client, userdata, msg):
    if msg.topic == "home/actuator/window":
        payload = json.loads(msg.payload.decode())
        command = payload.get("command")
        reason = payload.get("reason")
        
        # شبیه‌سازی فیزیکی موتور پنجره (فعال شدن رله‌ها)
        if command == "OPEN":
            print(f"\n🪟 🟢 RELAY TRIGGERED: Opening Window... (Reason: {reason})\n")
        elif command == "CLOSE":
            print(f"\n🪟 🔴 RELAY TRIGGERED: Closing Window... (Reason: {reason})\n")

client = mqtt.Client(client_id="RaspberryPi_Gateway")
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER_ADDRESS, PORT, 60)
client.loop_start()

print("🚀 Gateway is running. Simulating sensor data...")

try:
    while True:
        temp = round(random.uniform(18.0, 28.0), 2)
        hum = round(random.uniform(30.0, 60.0), 2)
        light = round(random.uniform(200, 800), 2)

        client.publish("home/sensor/temp/indoor", json.dumps({"value": temp, "unit": "C"}))
        client.publish("home/sensor/hum/indoor", json.dumps({"value": hum, "unit": "%"}))
        client.publish("home/sensor/light/indoor", json.dumps({"value": light, "unit": "lux"}))

        print(f"📤 Published -> Temp: {temp}°C | Hum: {hum}% | Light: {light} lux")
        time.sleep(5)
        
except KeyboardInterrupt:
    print("\n🛑 Gateway stopped by user.")
    client.loop_stop()
    client.disconnect()