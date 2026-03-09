import paho.mqtt.client as mqtt
import requests
import json
import time
import threading
import cherrypy

class ThingSpeakAdaptor:
    def __init__(self):
        self.write_api_key = "NISAWCFMEYGTH44W"
        self.read_api_key = "M45BE295HJ63IT3T"
        self.channel_id = "3279973" 
        self.broker = "broker.hivemq.com"
        self.port = 1883
        
        # حافظه موقت کپسوله شده در کلاس
        self.sensor_data = {
            "field1": None, "field2": None, "field3": None,
            "field4": None, "field5": None, "field6": None
        }
        
        self.mqtt_client = mqtt.Client(client_id="ThingSpeak_Adaptor_OOP")
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ ThingSpeak Adaptor Connected! Listening to all sensors...")
            self.mqtt_client.subscribe("home/sensor/+/+")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
            if isinstance(payload, dict): value = payload.get("value")
            else: value = float(payload)
            if value is None: return

            if "temp/indoor" in topic: self.sensor_data["field1"] = value
            elif "hum/indoor" in topic: self.sensor_data["field2"] = value
            elif "light/indoor" in topic: self.sensor_data["field3"] = value
            elif "temp/outdoor" in topic: self.sensor_data["field4"] = value
            elif "hum/outdoor" in topic: self.sensor_data["field5"] = value
            elif "light/outdoor" in topic: self.sensor_data["field6"] = value
        except:
            pass

    def upload_worker(self):
        while True:
            time.sleep(16)
            if any(v is not None for v in self.sensor_data.values()):
                url = f"https://api.thingspeak.com/update?api_key={self.write_api_key}"
                for i in range(1, 7):
                    field_key = f"field{i}"
                    if self.sensor_data[field_key] is not None:
                        url += f"&{field_key}={self.sensor_data[field_key]}"
                try:
                    response = requests.get(url)
                    if response.status_code == 200 and response.text != "0":
                        print(f"☁️ Uploaded to Cloud -> IN({self.sensor_data['field1']}°C) | OUT({self.sensor_data['field4']}°C)")
                except Exception as e:
                    print(f"❌ Error uploading to ThingSpeak: {e}")

    def start_mqtt(self):
        self.mqtt_client.connect(self.broker, self.port, 60)
        self.mqtt_client.loop_start()
        # اجرای آپلودر در پس‌زمینه
        threading.Thread(target=self.upload_worker, daemon=True).start()

    # CherryPy REST API برای خواندن تاریخچه
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def history(self):
        url = f"https://api.thingspeak.com/channels/{self.channel_id}/feeds.json?api_key={self.read_api_key}&results=10"
        try:
            response = requests.get(url)
            return response.json()
        except Exception as e:
            cherrypy.response.status = 500
            return {"error": str(e)}

if __name__ == '__main__':
    app = ThingSpeakAdaptor()
    app.start_mqtt()
    
    print("🌐 ThingSpeak CherryPy API is running on port 5000...")
    cherrypy.config.update({'server.socket_port': 5000, 'server.socket_host': '0.0.0.0'})
    cherrypy.quickstart(app)