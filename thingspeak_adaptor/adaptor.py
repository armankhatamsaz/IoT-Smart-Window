import paho.mqtt.client as mqtt
import requests
import json
import time
import threading
from flask import Flask, jsonify

WRITE_API_KEY = "NISAWCFMEYGTH44W"
READ_API_KEY = "M45BE295HJ63IT3T"
# آیدی کانال رو اینجا جایگذاری کن
CHANNEL_ID = "3279973" 

BROKER_ADDRESS = "broker.hivemq.com"
PORT = 1883

# حافظه موقت برای ذخیره آخرین دیتای سنسورها
sensor_data = {"field1": None, "field2": None, "field3": None}

# --- بخش آپلود به ThingSpeak (گوش دادن به MQTT) --- [cite: 104]
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ ThingSpeak Adaptor connected to MQTT!")
        # سابسکرایب به تاپیک‌های اندازه‌گیری [cite: 104]
        client.subscribe("home/sensor/temp/indoor")
        client.subscribe("home/sensor/hum/indoor")
        client.subscribe("home/sensor/light/indoor")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = json.loads(msg.payload.decode())
    value = payload.get("value")

    # آپدیت کردن حافظه موقت با آخرین مقادیر
    if "temp" in topic:
        sensor_data["field1"] = value
    elif "hum" in topic:
        sensor_data["field2"] = value
    elif "light" in topic:
        sensor_data["field3"] = value

mqtt_client = mqtt.Client(client_id="ThingSpeak_Adaptor_Service")
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(BROKER_ADDRESS, PORT, 60)
mqtt_client.loop_start()

# تابعی که هر 16 ثانیه دیتا رو به کلود میفرسته تا محدودیت اکانت رایگان دور زده بشه
def upload_worker():
    while True:
        time.sleep(16)
        if any(v is not None for v in sensor_data.values()):
            url = f"https://api.thingspeak.com/update?api_key={WRITE_API_KEY}"
            if sensor_data["field1"] is not None: url += f"&field1={sensor_data['field1']}"
            if sensor_data["field2"] is not None: url += f"&field2={sensor_data['field2']}"
            if sensor_data["field3"] is not None: url += f"&field3={sensor_data['field3']}"
            
            try:
                response = requests.get(url)
                if response.status_code == 200 and response.text != "0":
                    print(f"☁️ Uploaded to Cloud -> Temp: {sensor_data['field1']}, Hum: {sensor_data['field2']}, Light: {sensor_data['field3']}")
            except Exception as e:
                print(f"❌ Error uploading to ThingSpeak: {e}")

# اجرای عملیات آپلود در پس‌زمینه
threading.Thread(target=upload_worker, daemon=True).start()

# --- بخش REST API (ارائه سرویس وب برای تاریخچه) --- [cite: 104]
app = Flask(__name__)

@app.route('/history', methods=['GET'])
def get_history():
    # دریافت اندازه‌گیری‌های تاریخی از طریق REST API [cite: 107]
    url = f"https://api.thingspeak.com/channels/{CHANNEL_ID}/feeds.json?api_key={READ_API_KEY}&results=10"
    try:
        response = requests.get(url)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🌐 ThingSpeak REST API is running on port 5000...")
    app.run(port=5000, debug=False)