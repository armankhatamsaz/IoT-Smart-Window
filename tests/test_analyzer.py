import json
import time
from collections import deque

import pytest

from helpers import load, settings

analyzer = load("control_logic", "analyzer.py")
connector = load("gateway", "connector.py")


@pytest.fixture
def a():
    return analyzer.Analyzer(settings_file=settings("control_logic"))


def seed(a, temp, hum=45, light=400, out_temp=20, out_light=None):
    """Fill the sliding windows as if the same reading had arrived several times."""
    def fill(zone, sensor, value):
        a.history.setdefault(zone, {})[sensor] = deque([value] * a.window_size, maxlen=a.window_size)
    a.history = {}
    fill("livingroom", "temp", temp)
    fill("livingroom", "hum", hum)
    fill("livingroom", "light", light)
    fill("outdoor", "temp", out_temp)
    if out_light is not None:
        fill("outdoor", "light", out_light)


def target(a):
    value, reasons, _ = a.compute_target("livingroom")
    return value, reasons


def test_hot_room_with_cooler_outside_opens(a):
    seed(a, temp=28, out_temp=20)
    assert target(a)[0] > 40


def test_hot_room_with_hotter_outside_stays_closed(a):
    seed(a, temp=28, out_temp=32)
    assert target(a)[0] == 0


def test_cold_room_with_warmer_outside_opens(a):
    seed(a, temp=19, out_temp=25)
    assert target(a)[0] > 30


def test_comfortable_room_stays_closed(a):
    seed(a, temp=24, out_temp=30)
    assert target(a)[0] == 0


def test_humid_room_is_ventilated(a):
    seed(a, temp=24, hum=78, out_temp=30)
    assert target(a)[0] > 0


def test_humidity_does_not_override_temperature(a):
    # Ventilating a cold room with even colder air would only make it worse.
    seed(a, temp=14, hum=72, out_temp=12)
    value, reasons = target(a)
    assert value == 0
    assert "temperature has priority" in reasons[-1]


def test_strong_light_caps_the_opening(a):
    seed(a, temp=28, light=1500, out_temp=20)
    assert target(a)[0] == 40


def test_dark_room_on_a_bright_day_lets_daylight_in(a):
    seed(a, temp=24, light=200, out_light=1000)
    assert target(a)[0] == 25


def test_daylight_rule_does_not_flap(a):
    # 350 lx is too bright to open a closed window, but not bright enough to close it.
    seed(a, temp=24, light=350, out_light=1000)
    a.commanded = {"livingroom": 0}
    assert target(a)[0] == 0
    a.commanded = {"livingroom": 25}
    assert target(a)[0] == 25


def test_forecast_makes_it_act_early(a):
    seed(a, temp=25.5, out_temp=20)
    a.forecasts = {"livingroom": (27.0, time.time())}
    value, reasons = target(a)
    assert value > 0
    assert "acting early" in reasons[0]


def test_stale_forecast_is_ignored(a):
    seed(a, temp=25.5, out_temp=20)
    a.forecasts = {"livingroom": (27.0, time.time() - 600)}
    assert target(a)[0] == 0


def test_window_moves_gradually_and_settles(a):
    seed(a, temp=28, out_temp=20)
    sent = []

    class FakeClient:
        def publish(self, topic, payload, retain=False):
            sent.append(json.loads(payload)["position"])

    a.client = FakeClient()
    for _ in range(8):
        a.evaluate("livingroom")
    steps = [b - prev for prev, b in zip([0] + sent, sent)]
    assert sent and all(0 < s <= a.control["max_step"] for s in steps)
    count = len(sent)
    a.evaluate("livingroom")
    assert len(sent) == count  # settled: no repeated commands


def test_manual_command_pauses_automatic_control(a):
    seed(a, temp=30, out_temp=18)
    a.client = type("C", (), {"publish": lambda self, t, p, retain=False: pytest.fail("should not publish")})()
    a.override_until = {"livingroom": time.time() + 60}
    a.evaluate("livingroom")


class Msg:
    def __init__(self, topic, payload, retain=False):
        self.topic, self.payload, self.retain = topic, json.dumps(payload).encode(), retain


def test_restart_picks_up_the_retained_window_position(a):
    # After a restart the broker hands over the last command. The analyzer
    # must learn the real position from it, without pausing itself.
    a.on_message(None, None, Msg("home/livingroom/actuator/window",
                                 {"position": 60, "source": "manual"}, retain=True))
    assert a.commanded["livingroom"] == 60
    assert time.time() >= a.override_until.get("livingroom", 0)

    a.on_message(None, None, Msg("home/livingroom/actuator/window", {"position": 30, "source": "manual"}))
    assert a.commanded["livingroom"] == 30
    assert time.time() < a.override_until["livingroom"]


def test_glitches_are_rejected(a):
    assert a.store_sample("livingroom", "temp", 24.0)
    assert not a.store_sample("livingroom", "temp", 95.0)    # outside physical bounds
    assert not a.store_sample("livingroom", "temp", 44.0)    # jump of 20 C in one sample
    assert not a.store_sample("livingroom", "temp", "warm")  # not a number


def test_gateway_senml_is_understood_by_the_analyzer():
    gw = connector.Gateway.__new__(connector.Gateway)
    gw.topic_base = "livingroom"
    doc = json.loads(gw.senml("temp", 26.3))
    assert doc["bn"] == "home/livingroom/"
    assert doc["e"][0]["u"] == "Cel"
    assert analyzer.senml_value(doc) == 26.3
