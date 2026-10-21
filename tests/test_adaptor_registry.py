from datetime import datetime, timedelta

from helpers import load

adaptor_mod = load("thingspeak_adaptor", "adaptor.py")
registry = load("registry", "main.py")


class FakeResponse:
    def __init__(self, feeds):
        self.feeds = feeds

    def raise_for_status(self):
        pass

    def json(self):
        return {"feeds": self.feeds}


def bare_adaptor(channels):
    adaptor = adaptor_mod.ThingSpeakAdaptor.__new__(adaptor_mod.ThingSpeakAdaptor)
    adaptor.channels = channels
    adaptor.buffers = {c["name"]: {} for c in channels}
    return adaptor


def test_history_merges_channels_and_carries_values_forward(monkeypatch):
    adaptor = bare_adaptor([
        {"name": "temps", "channel_id": "1", "read_key": "r1",
         "mapping": {"home/livingroom/sensor/temp": "field1", "home/outdoor/sensor/temp": "field2"}},
        {"name": "windows", "channel_id": "2", "read_key": "r2",
         "mapping": {"home/livingroom/actuator/window": "field1"}},
    ])
    feeds = {
        "1": [{"created_at": "2026-01-01T10:00:00Z", "field1": "24.0", "field2": "18.0"},
              {"created_at": "2026-01-01T10:00:20Z", "field1": "24.5", "field2": "18.1"},
              {"created_at": "2026-01-01T10:00:40Z", "field1": "25.0", "field2": "18.2"}],
        # The window is only stored when it changes.
        "2": [{"created_at": "2026-01-01T10:00:05Z", "field1": "40"}],
    }
    monkeypatch.setattr(adaptor_mod.requests, "get",
                        lambda url, params, timeout: FakeResponse(feeds[url.split("/")[-2]]))

    samples = adaptor.read_history(10)
    assert [s["t"] for s in samples] == ["2026-01-01T10:00:00Z", "2026-01-01T10:00:20Z", "2026-01-01T10:00:40Z"]
    assert samples[0]["zones"]["livingroom"] == {"temp": 24.0}
    assert samples[1]["zones"]["livingroom"] == {"temp": 24.5, "window": 40.0}
    assert samples[2]["zones"]["livingroom"]["window"] == 40.0
    assert samples[2]["zones"]["outdoor"]["temp"] == 18.2


def test_window_position_is_queued_for_upload():
    adaptor = bare_adaptor([{"name": "c2", "api_key": "k2",
                             "mapping": {"home/livingroom/actuator/window": "field5"}}])

    class Msg:
        topic = "home/livingroom/actuator/window"
        payload = b'{"position": 60, "source": "auto"}'

    adaptor.on_message(None, None, Msg())
    assert adaptor.buffers["c2"] == {"field5": 60}


def test_passwords_never_leave_the_catalog():
    user = {"userID": 1, "userName": "user1", "password": "1234", "allowed_zones": ["Living Room"]}
    assert registry.public_user(user) == {"userID": 1, "userName": "user1", "allowed_zones": ["Living Room"]}


def test_entries_without_heartbeat_are_evicted():
    fmt = "%Y-%m-%d %H:%M:%S"
    now = datetime.now()
    fresh = now.strftime(fmt)
    old = (now - timedelta(seconds=registry.DEVICE_TTL_SECONDS + 60)).strftime(fmt)
    catalog = {"devicesList": [{"deviceID": "a", "lastUpdate": fresh}, {"deviceID": "b", "lastUpdate": old}],
               "servicesList": [{"serviceID": "s", "lastUpdate": old}]}
    assert registry.prune_stale(catalog)
    assert [d["deviceID"] for d in catalog["devicesList"]] == ["a"]
    assert catalog["servicesList"] == []
