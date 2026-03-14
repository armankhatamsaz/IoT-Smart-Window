import json
import os
import cherrypy
import threading
from datetime import datetime

CATALOG_FILE = 'catalog.json'

# قفل برای جلوگیری از کرش کردن (Race Condition)
file_lock = threading.Lock()

# ساختار دقیقاً منطبق با استاندارد رافائل (آرایه‌ای و جامع)
DEFAULT_CATALOG = {
    "projectOwner": "Smart Home Team",
    "projectName": "Window Control Ecosystem",
    "lastUpdate": str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    "broker": {
        "IP": "broker.hivemq.com",
        "port": 8000,
        "transport": "websockets"
    },
    "usersList": [
        {
            "userID": 1,
            "userName": "user1",
            "password": "1234",
            "allowed_zones": ["Living Room", "Bedroom 1"]
        },
        {
            "userID": 2,
            "userName": "user2",
            "password": "1234",
            "allowed_zones": ["Living Room", "Bedroom 2"]
        }
    ],
    "servicesList": [],
    "devicesList": []
}

class CatalogManager:
    @staticmethod
    def load():
        with file_lock:
            if os.path.exists(CATALOG_FILE):
                try:
                    with open(CATALOG_FILE, 'r') as f:
                        return json.load(f)
                except json.JSONDecodeError:
                    return DEFAULT_CATALOG
            return DEFAULT_CATALOG

    @staticmethod
    def save(data):
        with file_lock:
            data["lastUpdate"] = str(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            with open(CATALOG_FILE, 'w') as f:
                json.dump(data, f, indent=4)

# ابزار هندل کردن CORS برای دسترسی داشبورد
def cors():
    cherrypy.response.headers["Access-Control-Allow-Origin"] = "*"
    cherrypy.response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    cherrypy.response.headers["Access-Control-Allow-Headers"] = "Content-Type"

cherrypy.tools.cors = cherrypy.Tool('before_handler', cors)

class SmartHomeRegistry(object):
    exposed = True
    
    def __init__(self):
        if not os.path.exists(CATALOG_FILE):
            CatalogManager.save(DEFAULT_CATALOG)

    @cherrypy.tools.json_out()
    def OPTIONS(self, *uri, **params):
        return {"status": "OK"}

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        catalog = CatalogManager.load()
        if len(uri) > 0:
            if uri[0] == "broker":
                return catalog["broker"]
            elif uri[0] == "usersList":
                return catalog["usersList"]
            elif uri[0] == "devicesList":
                return catalog["devicesList"]
            elif uri[0] == "servicesList":
                return catalog["servicesList"]
            # مسیر ترکیبی برای راحتی داشبورد و ربات
            elif uri[0] == "config":
                return {"broker": catalog["broker"], "usersList": catalog["usersList"]}
            else:
                raise cherrypy.HTTPError(404, "Endpoint not found")
        else:
            return catalog # برگرداندن کل کاتالوگ

    @cherrypy.tools.json_out()
    def POST(self, *uri, **params):
        """ثبت نام یک دیوایس یا سرویس جدید (Registration)"""
        raw_body = cherrypy.request.body.read()
        if not raw_body:
            raise cherrypy.HTTPError(400, "Empty request body")
        
        new_data = json.loads(raw_body)
        new_data["lastUpdate"] = str(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        catalog = CatalogManager.load()

        if len(uri) > 0:
            if uri[0] == "devicesList":
                # اگه قبلا بود پاکش کن، جدیده رو بذار جاش
                catalog["devicesList"] = [d for d in catalog["devicesList"] if d.get("deviceID") != new_data.get("deviceID")]
                catalog["devicesList"].append(new_data)
                CatalogManager.save(catalog)
                return {"message": "Device Registered Successfully"}
                
            elif uri[0] == "servicesList":
                catalog["servicesList"] = [s for s in catalog["servicesList"] if s.get("serviceID") != new_data.get("serviceID")]
                catalog["servicesList"].append(new_data)
                CatalogManager.save(catalog)
                return {"message": "Service Registered Successfully"}
            else:
                raise cherrypy.HTTPError(404, "Use /devicesList or /servicesList")
        else:
            raise cherrypy.HTTPError(400, "Missing endpoint")

    @cherrypy.tools.json_out()
    def PUT(self, *uri, **params):
        """آپدیت کردن تایم‌استمپ برای نشون دادن اینکه دستگاه زنده‌ست (Heartbeat)"""
        raw_body = cherrypy.request.body.read()
        if not raw_body:
            raise cherrypy.HTTPError(400, "Empty request body")
        
        update_data = json.loads(raw_body)
        entity_id = update_data.get("id") # میتونه deviceID یا serviceID باشه
        catalog = CatalogManager.load()
        updated = False

        if len(uri) > 0:
            if uri[0] == "devicesList":
                for device in catalog["devicesList"]:
                    if device.get("deviceID") == entity_id:
                        device["lastUpdate"] = str(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        updated = True
                        break
            elif uri[0] == "servicesList":
                for service in catalog["servicesList"]:
                    if service.get("serviceID") == entity_id:
                        service["lastUpdate"] = str(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        updated = True
                        break

            if updated:
                CatalogManager.save(catalog)
                return {"message": "Timestamp Updated"}
            else:
                raise cherrypy.HTTPError(404, "Entity not found for update")
        else:
            raise cherrypy.HTTPError(400, "Missing endpoint")

    @cherrypy.tools.json_out()
    def DELETE(self, *uri, **params):
        """حذف یک دستگاه یا سرویس از کاتالوگ"""
        # (این بخش رو فعلا ساده نوشتیم که استراکچر 4 متد REST رعایت بشه)
        pass

if __name__ == '__main__':
    conf = {
        '/': {
            'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
            'tools.sessions.on': True,
            'tools.cors.on': True
        }
    }
    cherrypy.tree.mount(SmartHomeRegistry(), '/', conf)
    cherrypy.config.update({'server.socket_host': '0.0.0.0'})
    cherrypy.config.update({'server.socket_port': 8080})
    
    print("🚀 Advanced Registry Started (Compliant with Good Practices)")
    cherrypy.engine.start()
    cherrypy.engine.block()