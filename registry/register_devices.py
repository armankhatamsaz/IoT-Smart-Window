import requests
import time

# صبر می‌کنیم تا سرور رجیستری تو داکر کاملاً بالا بیاد
time.sleep(3)

# به جای 127.0.0.1 از اسم سرویس در داکر (registry) استفاده می‌کنیم
URL = 'http://registry:8080/devices'

# لیست کامل دستگاه‌ها
devices = [
    {"device_id": "temp_in_01", "type": "Temperature", "location": "Indoor", "mqtt_topic": "home/sensor/temp/indoor"},
    {"device_id": "hum_in_01", "type": "Humidity", "location": "Indoor", "mqtt_topic": "home/sensor/hum/indoor"},
    {"device_id": "light_in_01", "type": "Light", "location": "Indoor", "mqtt_topic": "home/sensor/light/indoor"},
    {"device_id": "temp_out_01", "type": "Temperature", "location": "Outdoor", "mqtt_topic": "home/sensor/temp/outdoor"},
    {"device_id": "hum_out_01", "type": "Humidity", "location": "Outdoor", "mqtt_topic": "home/sensor/hum/outdoor"},
    {"device_id": "light_out_01", "type": "Light", "location": "Outdoor", "mqtt_topic": "home/sensor/light/outdoor"},
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