import time
import random
import json
import paho.mqtt.client as mqtt

class SensorGateway:
    def __init__(self, broker, port):
        self.broker = broker
        self.port = port
        self.client = mqtt.Client(client_id="RaspberryPi_Gateway_OOP")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ Gateway Connected! Simulating sensors...")
            self.client.subscribe("home/actuator/window")
        else:
            print(f"❌ Connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        if msg.topic == "home/actuator/window":
            payload = json.loads(msg.payload.decode())
            command = payload.get("command")
            reason = payload.get("reason")
            
            if command == "OPEN":
                print(f"\n🪟 🟢 RELAY TRIGGERED: Opening Window... (Reason: {reason})\n")
            elif command == "CLOSE":
                print(f"\n🪟 🔴 RELAY TRIGGERED: Closing Window... (Reason: {reason})\n")

    def start(self):
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()
        
        try:
            while True:
                temp_in = round(random.uniform(20.0, 28.0), 2)
                hum_in = round(random.uniform(30.0, 60.0), 2)
                light_in = round(random.uniform(200, 800), 2)

                temp_out = round(random.uniform(15.0, 35.0), 2)
                hum_out = round(random.uniform(20.0, 80.0), 2)
                light_out = round(random.uniform(100, 5000), 2)

                self.client.publish("home/sensor/temp/indoor", json.dumps({"value": temp_in, "unit": "C"}))
                self.client.publish("home/sensor/hum/indoor", json.dumps({"value": hum_in, "unit": "%"}))
                self.client.publish("home/sensor/light/indoor", json.dumps({"value": light_in, "unit": "lux"}))

                self.client.publish("home/sensor/temp/outdoor", json.dumps({"value": temp_out, "unit": "C"}))
                self.client.publish("home/sensor/hum/outdoor", json.dumps({"value": hum_out, "unit": "%"}))
                self.client.publish("home/sensor/light/outdoor", json.dumps({"value": light_out, "unit": "lux"}))

                print(f"📤 IN  -> Temp: {temp_in}°C | Hum: {hum_in}% | Light: {light_in}lx")
                print(f"🌲 OUT -> Temp: {temp_out}°C | Hum: {hum_out}% | Light: {light_out}lx")
                print("-" * 50)
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n🛑 Gateway stopped.")
            self.client.loop_stop()
            self.client.disconnect()

if __name__ == "__main__":
    gateway = SensorGateway("broker.hivemq.com", 1883)
    gateway.start()