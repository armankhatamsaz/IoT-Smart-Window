import json
import os
import time
import cherrypy
import threading
from datetime import datetime

CATALOG_FILE = 'catalog.json'
# Devices/services that stop sending heartbeats are dropped after this many
# seconds (must be comfortably larger than the 50s heartbeat interval).
DEVICE_TTL_SECONDS = 150
# Collections managed at runtime, and the key identifying an entry in each.
ID_KEYS = {"devicesList": "deviceID", "servicesList": "serviceID", "usersList": "userID"}

# Guards catalog.json against concurrent access when several gateways
# register at the same time.
file_lock = threading.Lock()

DEFAULT_CATALOG = {
    "projectOwner": "Smart Home Team",
    "projectName": "Window Control Ecosystem",
    "lastUpdate": str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    # Services connect to IP/port; the dashboard runs in the browser, outside
    # Docker, so it gets its own WebSocket address.
    "broker": {
        "IP": "mosquitto",
        "port": 1883,
        "transport": "tcp",
        "webSocketURL": "ws://localhost:9001"
    },
    "usersList": [
        {"userID": 1, "userName": "user1", "password": "1234", "allowed_zones": ["Living Room", "Bedroom 1"]},
        {"userID": 2, "userName": "user2", "password": "1234", "allowed_zones": ["Living Room", "Bedroom 2"]}
    ],
    "servicesList": [],
    "devicesList": []
}


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def public_user(user):
    # Passwords stay inside the catalog; clients only ever see the rest.
    return {k: v for k, v in user.items() if k != "password"}


class CatalogManager:
    @staticmethod
    def load():
        if os.path.exists(CATALOG_FILE):
            try:
                with open(CATALOG_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return DEFAULT_CATALOG
        return DEFAULT_CATALOG

    @staticmethod
    def save(data):
        data["lastUpdate"] = timestamp()
        with open(CATALOG_FILE, 'w') as f:
            json.dump(data, f, indent=4)


def prune_stale(catalog):
    """Remove devices/services that have not refreshed within the TTL.
    Returns True if anything was removed."""
    now = datetime.now()

    def is_fresh(entry):
        stamp = entry.get("lastUpdate")
        if not stamp:
            return True
        try:
            age = (now - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")).total_seconds()
            return age <= DEVICE_TTL_SECONDS
        except ValueError:
            return True

    changed = False
    for key in ("devicesList", "servicesList"):
        current = catalog.get(key, [])
        kept = [e for e in current if is_fresh(e)]
        if len(kept) != len(current):
            catalog[key] = kept
            changed = True
    return changed


def cors():
    cherrypy.response.headers["Access-Control-Allow-Origin"] = "*"
    cherrypy.response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    cherrypy.response.headers["Access-Control-Allow-Headers"] = "Content-Type"

cherrypy.tools.cors = cherrypy.Tool('before_handler', cors)


class SmartHomeRegistry(object):
    exposed = True

    def __init__(self):
        with file_lock:
            if not os.path.exists(CATALOG_FILE):
                CatalogManager.save(DEFAULT_CATALOG)

    @cherrypy.tools.json_out()
    def OPTIONS(self, *uri, **params):
        return {"status": "OK"}

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        with file_lock:
            catalog = CatalogManager.load()
            if prune_stale(catalog):
                CatalogManager.save(catalog)

        users = [public_user(u) for u in catalog["usersList"]]
        if len(uri) == 0:
            return dict(catalog, usersList=users)
        if uri[0] == "broker":
            return catalog["broker"]
        if uri[0] == "config":
            return {"broker": catalog["broker"], "usersList": users}
        if uri[0] in ID_KEYS:
            entries = users if uri[0] == "usersList" else catalog[uri[0]]
            if len(uri) > 1:
                # /<collection>/<id> returns a single entry.
                id_key = ID_KEYS[uri[0]]
                entry = next((e for e in entries if str(e.get(id_key)) == uri[1]), None)
                if entry is None:
                    raise cherrypy.HTTPError(404, f"'{uri[1]}' not found in {uri[0]}")
                return entry
            return entries
        raise cherrypy.HTTPError(404, "Endpoint not found")

    @cherrypy.tools.json_out()
    def POST(self, *uri, **params):
        if len(uri) > 0 and uri[0] == "login":
            return self._login()

        collection = self._collection(uri)
        id_key = ID_KEYS[collection]
        new_data = self._body()

        if new_data.get(id_key) is None:
            raise cherrypy.HTTPError(400, f"Missing '{id_key}' in request body")
        new_data["lastUpdate"] = timestamp()

        # Hold the lock for the whole read-modify-write so a registration is
        # never interleaved with another writer.
        with file_lock:
            catalog = CatalogManager.load()
            entries = catalog.setdefault(collection, [])
            # Re-registering with a known ID replaces the previous entry.
            catalog[collection] = [e for e in entries if e.get(id_key) != new_data[id_key]]
            catalog[collection].append(new_data)
            CatalogManager.save(catalog)
        return {"message": f"'{new_data[id_key]}' registered in {collection}"}

    @cherrypy.tools.json_out()
    def PUT(self, *uri, **params):
        collection = self._collection(uri)
        id_key = ID_KEYS[collection]
        body = self._body()
        entity_id = body.get("id", body.get(id_key))

        with file_lock:
            catalog = CatalogManager.load()
            for entry in catalog.get(collection, []):
                if str(entry.get(id_key)) == str(entity_id):
                    # A heartbeat only refreshes the timestamp; any other field
                    # in the body updates the entry in place.
                    entry.update({k: v for k, v in body.items() if k != "id"})
                    entry["lastUpdate"] = timestamp()
                    CatalogManager.save(catalog)
                    return {"message": f"'{entity_id}' updated in {collection}"}
        raise cherrypy.HTTPError(404, f"'{entity_id}' not found in {collection}")

    @cherrypy.tools.json_out()
    def DELETE(self, *uri, **params):
        collection = self._collection(uri)
        if len(uri) < 2:
            raise cherrypy.HTTPError(400, f"Use /{collection}/<id>")

        id_key = ID_KEYS[collection]
        entity_id = uri[1]

        with file_lock:
            catalog = CatalogManager.load()
            entries = catalog.get(collection, [])
            kept = [e for e in entries if str(e.get(id_key)) != str(entity_id)]
            if len(kept) == len(entries):
                raise cherrypy.HTTPError(404, f"'{entity_id}' not found in {collection}")
            catalog[collection] = kept
            CatalogManager.save(catalog)
        return {"message": f"'{entity_id}' removed from {collection}"}

    # --- helpers ------------------------------------------------------------

    def _login(self):
        # Credentials are checked here, so clients never need the password list.
        body = self._body()
        with file_lock:
            catalog = CatalogManager.load()
        user = next((u for u in catalog["usersList"]
                     if u.get("userName") == body.get("userName")
                     and u.get("password") == body.get("password")), None)
        if user is None:
            raise cherrypy.HTTPError(401, "Wrong username or password")
        return public_user(user)

    @staticmethod
    def _collection(uri):
        if len(uri) == 0 or uri[0] not in ID_KEYS:
            raise cherrypy.HTTPError(404, "Use /devicesList, /servicesList or /usersList")
        return uri[0]

    @staticmethod
    def _body():
        raw_body = cherrypy.request.body.read()
        if not raw_body:
            raise cherrypy.HTTPError(400, "Empty request body")
        return json.loads(raw_body)


if __name__ == '__main__':
    conf = {
        '/': {
            'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
            'tools.sessions.on': True,
            'tools.cors.on': True
        }
    }

    def janitor():
        # Periodically evict stale entries even when nobody is querying.
        while True:
            time.sleep(30)
            with file_lock:
                catalog = CatalogManager.load()
                if prune_stale(catalog):
                    CatalogManager.save(catalog)

    threading.Thread(target=janitor, daemon=True).start()

    cherrypy.tree.mount(SmartHomeRegistry(), '/', conf)
    cherrypy.config.update({'server.socket_host': '0.0.0.0'})
    cherrypy.config.update({'server.socket_port': 8080})

    cherrypy.engine.start()
    cherrypy.engine.block()
