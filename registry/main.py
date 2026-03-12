import json
import os
import cherrypy
import threading

CATALOG_FILE = 'catalog.json'

# این قفل (Lock) باعث میشه میکروسرویس‌ها برای ثبت‌نام تو صف بایستن و فایل خراب نشه
file_lock = threading.Lock()

DEFAULT_CATALOG = {
    "broker": {"host": "broker.hivemq.com", "port": 8000, "transport": "websockets"},
    "users": [
        {"username": "user1", "password": "1234", "allowed_rooms": ["Living Room", "Bedroom 1"]},
        {"username": "user2", "password": "1234", "allowed_rooms": ["Living Room", "Bedroom 2"]}
    ],
    "devices": [],
    "services": []
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
                    return DEFAULT_CATALOG # اگه فایل خراب شد، دیفالت رو برگردون
            return DEFAULT_CATALOG

    @staticmethod
    def save(data):
        with file_lock:
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
        return {"status": "OK"} # جواب دادن به درخواست‌های پیش‌نیازِ مرورگر

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        catalog = CatalogManager.load()
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
            return {"message": "Registry API is running!"}

    @cherrypy.tools.json_out()
    def POST(self, *uri, **params):
        raw_body = cherrypy.request.body.read()
        if not raw_body:
            raise cherrypy.HTTPError(400, "Empty request body")
        
        new_data = json.loads(raw_body)
        catalog = CatalogManager.load()

        if len(uri) > 0:
            if uri[0] == "devices":
                catalog["devices"] = [d for d in catalog["devices"] if d.get("device_id") != new_data.get("device_id")]
                catalog["devices"].append(new_data)
                CatalogManager.save(catalog)
                return {"message": "Device Registered"}
                
            elif uri[0] == "services":
                catalog["services"] = [s for s in catalog["services"] if s.get("service_name") != new_data.get("service_name")]
                catalog["services"].append(new_data)
                CatalogManager.save(catalog)
                return {"message": "Service Registered"}
            else:
                raise cherrypy.HTTPError(404, "Endpoint not found")
        else:
            raise cherrypy.HTTPError(400, "Missing endpoint")

    def PUT(self, *uri, **params): pass
    def DELETE(self, *uri, **params): pass

if __name__ == '__main__':
    conf = {
        '/': {
            'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
            'tools.sessions.on': True,
            'tools.cors.on': True  # فعال کردن CORS برای داشبورد
        }
    }
    cherrypy.tree.mount(SmartHomeRegistry(), '/', conf)
    cherrypy.config.update({'server.socket_host': '0.0.0.0'})
    cherrypy.config.update({'server.socket_port': 8080})
    cherrypy.engine.start()
    cherrypy.engine.block()