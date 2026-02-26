import paho.mqtt.client as mqtt
import json

BROKER_ADDRESS = "broker.hivemq.com"
PORT = 1883

# تعریف حد آستانه‌ها (Thresholds)
TEMP_HIGH = 25.0
TEMP_LOW = 20.0
HUM_HIGH = 50.0

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ Brain Connected! Listening for data...")
        client.subscribe("home/sensor/temp/indoor")
        client.subscribe("home/sensor/hum/indoor")
        client.subscribe("home/sensor/light/indoor")
    else:
        print(f"❌ Connection failed: {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = json.loads(msg.payload.decode())
    value = payload.get("value")
    
    # چاپ دیتای دریافتی
    unit = payload.get("unit")
    print(f"🧠 Received -> Topic: {topic} | Value: {value} {unit}")
    
    # لاجیک تصمیم‌گیری
    command = None
    reason = ""
    
    if "temp" in topic:
        if value > TEMP_HIGH:
            command = "OPEN"
            reason = f"Temperature too high ({value}°C)"
        elif value < TEMP_LOW:
            command = "CLOSE"
            reason = f"Temperature too low ({value}°C)"
            
    elif "hum" in topic:
        if value > HUM_HIGH:
            command = "OPEN"
            reason = f"Humidity too high ({value}%)"
    
    # اگر تصمیمی گرفته شد، آن را به عنوان یک دستور به اکچویتور می‌فرستیم
    if command:
        actuator_topic = "home/actuator/window"
        actuator_payload = json.dumps({"command": command, "reason": reason})
        client.publish(actuator_topic, actuator_payload)
        print(f"⚡ ACTION: Sending command [{command}] to Window! Reason: {reason}")

client = mqtt.Client(client_id="ControlLogic_Brain")
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER_ADDRESS, PORT, 60)
client.loop_forever()