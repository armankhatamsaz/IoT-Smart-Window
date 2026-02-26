import paho.mqtt.client as mqtt
import json

BROKER_ADDRESS = "broker.hivemq.com"
PORT = 1883

TEMP_HIGH_INDOOR = 25.0
HUM_HIGH_INDOOR = 55.0

# حافظه برای ذخیره آخرین وضعیت سنسورها
state = {
    "temp_in": 22.0, "temp_out": 22.0,
    "hum_in": 40.0, "hum_out": 40.0
}

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ Smart Brain Connected! Listening to all sensors...")
        client.subscribe("home/sensor/+/+")
    else:
        print(f"❌ Connection failed: {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    
    try:
        # تلاش برای خواندن دیتا
        payload = json.loads(msg.payload.decode())
        
        # بررسی اینکه آیا دیتا دیکشنری است (فرمت ما) یا عدد خالی (پیام‌های متفرقه شبکه)
        if isinstance(payload, dict):
            value = payload.get("value")
        else:
            value = float(payload)
            
        if value is None:
            return
            
        # آپدیت کردن حافظه سیستم
        if "temp/indoor" in topic: state["temp_in"] = value
        elif "temp/outdoor" in topic: state["temp_out"] = value
        elif "hum/indoor" in topic: state["hum_in"] = value
        elif "hum/outdoor" in topic: state["hum_out"] = value

        command = None
        reason = ""

        # منطق تصمیم‌گیری هوشمند
        if "temp" in topic:
            if state["temp_in"] > TEMP_HIGH_INDOOR:
                if state["temp_out"] < state["temp_in"]:
                    command = "OPEN"
                    reason = f"Cooling: Indoor is hot ({state['temp_in']}°C) but Outdoor is cooler ({state['temp_out']}°C)"
                else:
                    command = "CLOSE"
                    reason = f"Isolate: Indoor is hot ({state['temp_in']}°C) but Outdoor is HOTTER ({state['temp_out']}°C)!"
                    
        elif "hum" in topic:
            if state["hum_in"] > HUM_HIGH_INDOOR:
                command = "OPEN"
                reason = f"Ventilation: Indoor humidity too high ({state['hum_in']}%)"

        if command:
            client.publish("home/actuator/window", json.dumps({"command": command, "reason": reason}))
            print(f"⚡ ACTION: {command} -> {reason}")
            
    except Exception as e:
        # اگر دیتای نامعتبری آمد، آن را نادیده بگیر تا برنامه کرش نکند
        pass

client = mqtt.Client(client_id="ControlLogic_Brain_V2")
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER_ADDRESS, PORT, 60)
client.loop_forever()