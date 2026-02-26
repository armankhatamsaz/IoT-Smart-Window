import time
import random
import json
import paho.mqtt.client as mqtt

BROKER_ADDRESS = "broker.hivemq.com"
PORT = 1883

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ Gateway Connected! Simulating Indoor & Outdoor sensors...")
        client.subscribe("home/actuator/window")
    else:
        print(f"❌ Connection failed: {rc}")

def on_message(client, userdata, msg):
    if msg.topic == "home/actuator/window":
        payload = json.loads(msg.payload.decode())
        command = payload.get("command")
        reason = payload.get("reason")
        
        if command == "OPEN":
            print(f"\n🪟 🟢 RELAY TRIGGERED: Opening Window... (Reason: {reason})\n")
        elif command == "CLOSE":
            print(f"\n🪟 🔴 RELAY TRIGGERED: Closing Window... (Reason: {reason})\n")

client = mqtt.Client(client_id="RaspberryPi_Gateway_V2")
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER_ADDRESS, PORT, 60)
client.loop_start()

try:
    while True:
        # شبیه‌سازی داده‌های داخل اتاق
        temp_in = round(random.uniform(20.0, 28.0), 2)
        hum_in = round(random.uniform(30.0, 60.0), 2)
        light_in = round(random.uniform(200, 800), 2)

        # شبیه‌سازی داده‌های بیرون اتاق (معمولا متغیرتر است)
        temp_out = round(random.uniform(15.0, 35.0), 2)
        hum_out = round(random.uniform(20.0, 80.0), 2)
        light_out = round(random.uniform(100, 5000), 2)

        # ارسال داده‌های داخل
        client.publish("home/sensor/temp/indoor", json.dumps({"value": temp_in, "unit": "C"}))
        client.publish("home/sensor/hum/indoor", json.dumps({"value": hum_in, "unit": "%"}))
        client.publish("home/sensor/light/indoor", json.dumps({"value": light_in, "unit": "lux"}))

        # ارسال داده‌های بیرون
        client.publish("home/sensor/temp/outdoor", json.dumps({"value": temp_out, "unit": "C"}))
        client.publish("home/sensor/hum/outdoor", json.dumps({"value": hum_out, "unit": "%"}))
        client.publish("home/sensor/light/outdoor", json.dumps({"value": light_out, "unit": "lux"}))

        print(f"📤 IN  -> Temp: {temp_in}°C | Hum: {hum_in}% | Light: {light_in}lx")
        print(f"🌲 OUT -> Temp: {temp_out}°C | Hum: {hum_out}% | Light: {light_out}lx")
        print("-" * 50)
        time.sleep(5)
        
except KeyboardInterrupt:
    print("\n🛑 Gateway stopped.")
    client.loop_stop()
    client.disconnect()