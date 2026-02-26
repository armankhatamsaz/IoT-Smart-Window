import requests

URL = 'http://127.0.0.1:8080/devices'

# لیست دستگاه‌هایی که طبق پروپوزال باید در سیستم ثبت بشن
devices = [
    {"device_id": "temp_in_01", "type": "Temperature", "location": "Indoor", "mqtt_topic": "home/sensor/temp/indoor"},
    {"device_id": "hum_in_01", "type": "Humidity", "location": "Indoor", "mqtt_topic": "home/sensor/hum/indoor"},
    {"device_id": "light_in_01", "type": "Light", "location": "Indoor", "mqtt_topic": "home/sensor/light/indoor"},
    {"device_id": "window_actuator_01", "type": "Actuator", "location": "Living Room", "mqtt_topic": "home/actuator/window"}
]

for dev in devices:
    try:
        response = requests.post(URL, json=dev)
        if response.status_code == 201:
            print(f"✅ Successfully registered: {dev['device_id']}")
        else:
            print(f"❌ Failed to register {dev['device_id']}")
    except Exception as e:
        print(f"Error connecting to Registry: {e}")