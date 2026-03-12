import json
import os
import cherrypy

CATALOG_FILE = 'catalog.json'

# دیتابیس اولیه با 2 یوزر و 3 اتاق (طبق سناریوی خفنی که چیدیم)
DEFAULT_CATALOG = {
    "broker": {
        "host": "broker.hivemq.com",
        "port": 8000,
        "transport": "websockets"
    },
    "users": [
        {
            "username": "user1",
            "password": "1234",
            "allowed_rooms": ["Living Room", "Bedroom 1"]
        },
        {
            "username": "user2",
            "password": "1234",
            "allowed_rooms": ["Living Room", "Bedroom 2"]
        }
    ],
    "devices": [],
    "services": []
}

class CatalogManager:
    """کلاس کمکی برای مدیریت خوندن و نوشتن فایل کاتالوگ"""
    @staticmethod
    def load():
        if os.path.exists(CATALOG_FILE):
            with open(CATALOG_FILE, 'r') as f:
                return json.load(f)
        return DEFAULT_CATALOG

    @staticmethod
    def save(data):
        with open(CATALOG_FILE, 'w') as f:
            json.dump(data, f, indent=4)


# ==========================================
# وب‌سرویس اصلی (دقیقاً با استایل MethodDispatcher استاد)
# ==========================================
class SmartHomeRegistry(object):
    exposed = True
    
    def __init__(self):
        # ساخت فایل اولیه در صورت عدم وجود
        if not os.path.exists(CATALOG_FILE):
            CatalogManager.save(DEFAULT_CATALOG)

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        catalog = CatalogManager.load()
        
        # هندل کردن مسیرها با استفاده از uri[0] دقیقاً مثل کد استاد
        if len(uri) > 0:
            if uri[0] == "config":
                return {"broker": catalog["broker"], "users": catalog["users"]}
            elif uri[0] == "devices":
                return catalog["devices"]
            elif uri[0] == "services":
                return catalog["services"]
            else:
                raise cherrypy.HTTPError(404, "Endpoint not found")
        else:
            return {"message": "Smart Home Registry API is running! Available endpoints: /config, /devices, /services"}

    @cherrypy.tools.json_out()
    def POST(self, *uri, **params):
        # خوندن بادیِ درخواست (ورودی JSON)
        raw_body = cherrypy.request.body.read()
        if not raw_body:
            raise cherrypy.HTTPError(400, "Empty request body")
        
        new_data = json.loads(raw_body)
        catalog = CatalogManager.load()

        if len(uri) > 0:
            if uri[0] == "devices":
                # آپدیت لیست دیوایس‌ها (جلوگیری از تکرار)
                catalog["devices"] = [d for d in catalog["devices"] if d.get("device_id") != new_data.get("device_id")]
                catalog["devices"].append(new_data)
                CatalogManager.save(catalog)
                return {"message": "Device Registered Successfully", "device": new_data}
                
            elif uri[0] == "services":
                # آپدیت لیست سرویس‌ها
                catalog["services"] = [s for s in catalog["services"] if s.get("service_name") != new_data.get("service_name")]
                catalog["services"].append(new_data)
                CatalogManager.save(catalog)
                return {"message": "Service Registered Successfully"}
            else:
                raise cherrypy.HTTPError(404, "Endpoint not found")
        else:
            raise cherrypy.HTTPError(400, "Missing endpoint (e.g., /devices or /services)")

    def PUT(self, *uri, **params):
        pass

    def DELETE(self, *uri, **params):
        pass


if __name__ == '__main__':
    # پیکربندی دقیقا طبق نمونه استاد (استفاده از MethodDispatcher)
    conf = {
        '/': {
            'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
            'tools.sessions.on': True
        }
    }
    
    # مونت کردن روت اصلی
    cherrypy.tree.mount(SmartHomeRegistry(), '/', conf)

    print("🚀 Starting Object-Oriented Smart Home Registry on http://0.0.0.0:8080 ...")
    cherrypy.config.update({'server.socket_host': '0.0.0.0'})
    cherrypy.config.update({'server.socket_port': 8080})
    
    # استارت سرور به روش استاد
    cherrypy.engine.start()
    cherrypy.engine.block()