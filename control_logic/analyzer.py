import paho.mqtt.client as mqtt
import json

class SmartAnalyzer:
    def __init__(self, broker, port):
        self.broker = broker
        self.port = port
        self.temp_high_indoor = 25.0
        self.hum_high_indoor = 55.0
        
        # وضعیت سیستم (State) به عنوان متغیر کلاس
        self.state = {
            "temp_in": 22.0, "temp_out": 22.0,
            "hum_in": 40.0, "hum_out": 40.0
        }
        
        self.client = mqtt.Client(client_id="ControlLogic_Brain_OOP")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("✅ Smart Brain Connected! Listening to all sensors...")
            self.client.subscribe("home/sensor/+/+")
        else:
            print(f"❌ Connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
            if isinstance(payload, dict): value = payload.get("value")
            else: value = float(payload)
            if value is None: return
                
            if "temp/indoor" in topic: self.state["temp_in"] = value
            elif "temp/outdoor" in topic: self.state["temp_out"] = value
            elif "hum/indoor" in topic: self.state["hum_in"] = value
            elif "hum/outdoor" in topic: self.state["hum_out"] = value

            # فراخوانی متد پردازش لاجیک
            self.evaluate_conditions(topic)
                
        except Exception:
            pass

    def evaluate_conditions(self, topic):
        command = None
        reason = ""

        if "temp" in topic:
            if self.state["temp_in"] > self.temp_high_indoor:
                if self.state["temp_out"] < self.state["temp_in"]:
                    command = "OPEN"
                    reason = f"Cooling: Indoor is hot ({self.state['temp_in']}°C) but Outdoor is cooler ({self.state['temp_out']}°C)"
                else:
                    command = "CLOSE"
                    reason = f"Isolate: Indoor is hot ({self.state['temp_in']}°C) but Outdoor is HOTTER ({self.state['temp_out']}°C)!"
                    
        elif "hum" in topic:
            if self.state["hum_in"] > self.hum_high_indoor:
                command = "OPEN"
                reason = f"Ventilation: Indoor humidity too high ({self.state['hum_in']}%)"

        if command:
            self.client.publish("home/actuator/window", json.dumps({"command": command, "reason": reason}))
            print(f"⚡ ACTION: {command} -> {reason}")

    def start(self):
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_forever()

if __name__ == "__main__":
    analyzer = SmartAnalyzer("broker.hivemq.com", 1883)
    analyzer.start()