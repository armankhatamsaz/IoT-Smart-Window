import json
import os
import cherrypy

CATALOG_FILE = 'catalog.json'

class DeviceRegistry:
    def __init__(self):
        # هنگام بالا آمدن کلاس، اگر فایل وجود نداشت آن را می‌سازد
        if not os.path.exists(CATALOG_FILE):
            self.save_catalog({"devices": [], "services": []})

    def load_catalog(self):
        if os.path.exists(CATALOG_FILE):
            with open(CATALOG_FILE, 'r') as f:
                return json.load(f)
        return {"devices": [], "services": []}

    def save_catalog(self, data):
        with open(CATALOG_FILE, 'w') as f:
            json.dump(data, f, indent=4)

    # مسیر اصلی سرور (http://localhost:8080/)
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def index(self):
        return {"message": "Welcome to the Smart Window Device & Service Registry (OOP & CherryPy)!"}

    # مسیر مدیریت دستگاه‌ها (http://localhost:8080/devices)
    @cherrypy.expose
    @cherrypy.tools.json_out()
    @cherrypy.tools.json_in()
    def devices(self):
        if cherrypy.request.method == 'GET':
            catalog = self.load_catalog()
            return catalog["devices"]
        
        elif cherrypy.request.method == 'POST':
            new_device = cherrypy.request.json
            catalog = self.load_catalog()
            catalog["devices"].append(new_device)
            self.save_catalog(catalog)
            cherrypy.response.status = 201
            return {"message": "Device registered successfully!", "device": new_device}

if __name__ == '__main__':
    # تنظیم پورت روی 8080 و دسترسی برای همه IP ها (جهت سازگاری با داکر در آینده)
    cherrypy.config.update({'server.socket_port': 8080, 'server.socket_host': '0.0.0.0'})
    # استارت سرور با کلاس DeviceRegistry
    cherrypy.quickstart(DeviceRegistry())