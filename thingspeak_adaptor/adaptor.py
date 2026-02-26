import paho.mqtt.client as mqtt
import requests
import json
import time
import threading
from flask import Flask, jsonify

WRITE_API_KEY = "NISAWCFMEYGTH44W"
READ_API_KEY = "M45BE295HJ63IT3T"
CHANNEL_ID = "3279973" 

BROKER_ADDRESS = "broker.hivemq.com"
PORT = 1883

# حافظه موقت برای ذخیره 6 دیتا (داخل و بیرون)
sensor_data = {
    "field1": None, "field2": None, "field3": None, # Indoor
    "field4": None, "field5": None, "field6": None  # Outdoor
}

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ ThingSpeak Adaptor Connected! Listening to all sensors...")
        # سابسکرایب به تمام تاپیک‌های سنسورها
        client.subscribe("home/sensor/+/+")
    else:
        print(f"❌ Connection failed: {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
        
        # تشخیص فرمت (دیکشنری یا عدد خالی)
        if isinstance(payload, dict): value = payload.get("value")
        else: value = float(payload)
            
        if value is None: return

        # تخصیص داده‌ها به فیلدهای متناظر در ThingSpeak
        if "temp/indoor" in topic: sensor_data["field1"] = value
        elif "hum/indoor" in topic: sensor_data["field2"] = value
        elif "light/indoor" in topic: sensor_data["field3"] = value
        elif "temp/outdoor" in topic: sensor_data["field4"] = value
        elif "hum/outdoor" in topic: sensor_data["field5"] = value
        elif "light/outdoor" in topic: sensor_data["field6"] = value
    except:
        pass

mqtt_client = mqtt.Client(client_id="ThingSpeak_Adaptor_V2")
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(BROKER_ADDRESS, PORT, 60)
mqtt_client.loop_start()

# تابعی که هر 16 ثانیه کل 6 دیتا رو یکجا به کلود می‌فرسته
def upload_worker():
    while True:
        time.sleep(16)
        if any(v is not None for v in sensor_data.values()):
            url = f"https://api.thingspeak.com/update?api_key={WRITE_API_KEY}"
            
            # چسباندن تمام فیلدهایی که مقدار دارند به آدرس
            for i in range(1, 7):
                field_key = f"field{i}"
                if sensor_data[field_key] is not None:
                    url += f"&{field_key}={sensor_data[field_key]}"
            
            try:
                response = requests.get(url)
                if response.status_code == 200 and response.text != "0":
                    print(f"☁️ Uploaded to Cloud -> IN({sensor_data['field1']}°C) | OUT({sensor_data['field4']}°C)")
            except Exception as e:
                print(f"❌ Error uploading to ThingSpeak: {e}")

threading.Thread(target=upload_worker, daemon=True).start()

app = Flask(__name__)

@app.route('/history', methods=['GET'])
def get_history():
    url = f"https://api.thingspeak.com/channels/{CHANNEL_ID}/feeds.json?api_key={READ_API_KEY}&results=10"
    try:
        response = requests.get(url)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🌐 ThingSpeak REST API is running on port 5000...")
    app.run(port=5000, debug=False)