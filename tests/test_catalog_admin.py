import socket

import cherrypy
import pytest
import requests

from helpers import load

registry = load("registry", "main.py")
admin_mod = load("tools", "catalog_admin.py")


@pytest.fixture(scope="module")
def registry_url(tmp_path_factory):
    """Run the real registry on a free port, with its catalog in a temp folder."""
    registry.CATALOG_FILE = str(tmp_path_factory.mktemp("catalog") / "catalog.json")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    conf = {'/': {'request.dispatch': cherrypy.dispatch.MethodDispatcher()}}
    cherrypy.tree.mount(registry.SmartHomeRegistry(), '/', conf)
    cherrypy.config.update({'server.socket_host': '127.0.0.1', 'server.socket_port': port,
                            'log.screen': False, 'engine.autoreload.on': False})
    cherrypy.engine.start()
    url = f"http://127.0.0.1:{port}"
    # One registered gateway, so "Living Room" is a known zone.
    requests.post(f"{url}/devicesList", json={"deviceID": "gw_test", "zone": "Living Room",
                                              "availableServices": ["MQTT"]})
    yield url
    cherrypy.engine.exit()


def login(url, name, password):
    return requests.post(f"{url}/login", json={"userName": name, "password": password})


def test_a_new_user_can_log_in_right_away(registry_url):
    admin = admin_mod.CatalogAdmin(registry_url)
    user_id = admin.add_user("user3", "pw3", ["Living Room"])
    assert user_id == 3  # the two default users are 1 and 2
    resp = login(registry_url, "user3", "pw3")
    assert resp.status_code == 200
    assert resp.json()["allowed_zones"] == ["Living Room"]
    assert "password" not in resp.json()


def test_zones_must_belong_to_a_registered_gateway(registry_url):
    admin = admin_mod.CatalogAdmin(registry_url)
    with pytest.raises(ValueError):
        admin.add_user("user4", "pw4", ["Garage"])
    with pytest.raises(ValueError):
        admin.set_zones(1, ["Garage"])


def test_zones_change_and_user_is_removed(registry_url):
    admin = admin_mod.CatalogAdmin(registry_url)
    admin.set_zones(1, ["Living Room"])
    assert requests.get(f"{registry_url}/usersList/1").json()["allowed_zones"] == ["Living Room"]

    admin.remove_user(3)
    assert login(registry_url, "user3", "pw3").status_code == 401
    with pytest.raises(requests.HTTPError):
        admin.remove_user(3)  # already gone: the registry answers 404
