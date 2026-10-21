import json
import time
import random

from helpers import load, settings

connector = load("gateway", "connector.py")
analyzer = load("control_logic", "analyzer.py")
forecaster = load("forecaster", "forecaster.py")


def simulate_history(hours=4, seed=5):
    """Run the gateway physics and the real analyzer in closed loop, and record
    the history the way the adaptor serves it (one row every 20 s)."""
    random.seed(seed)
    clock = [0.0]
    day = connector.DAY_MINUTES * 60
    connector.sim_hour = lambda: (clock[0] % day) / day * 24

    def gateway(zone):
        gw = connector.Gateway.__new__(connector.Gateway)
        gw.zone_name = gw.topic_base = zone
        gw.is_outdoor = zone == "outdoor"
        gw.window_position, gw.indoor_temp, gw.indoor_hum = 0, 22.0, 50.0
        return gw

    room, outdoor = gateway("livingroom"), gateway("outdoor")
    control = analyzer.Analyzer(settings_file=settings("control_logic"))

    class FakeClient:
        def publish(self, topic, payload, retain=False):
            room.window_position = json.loads(payload)["position"]

    control.client = FakeClient()
    start = 1_780_000_000
    samples = []
    for step in range(int(hours * 3600 / 5)):
        clock[0] = step * 5
        out, inside = outdoor.sample_outdoor(), room.sample_indoor()
        for name in ("temp", "hum", "light"):
            control.store_sample("outdoor", name, out[name])
            control.store_sample("livingroom", name, inside[name])
        control.evaluate("livingroom")
        if step % 4 == 0:
            samples.append({
                "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start + step * 5)),
                "zones": {
                    "livingroom": dict(inside, window=float(room.window_position)),
                    "outdoor": out,
                },
            })
    return samples


def new_forecaster():
    fc = forecaster.Forecaster(settings_file=settings("forecaster"))
    fc.step_seconds = 20.0
    return fc


def test_model_beats_the_naive_guess():
    info = new_forecaster().train_zone("livingroom", simulate_history())
    assert info["usable"]
    assert info["mae"] < info["mae_naive"] / 2


def test_model_learns_the_physics_of_the_room():
    info = new_forecaster().train_zone("livingroom", simulate_history())
    coefficients = info["coefficients"]
    # Every effect has the physical sign: warmer outside, an open window and
    # sunshine all push the room temperature in the expected direction.
    assert all(value > 0 for value in coefficients.values())
    # Over the one-minute horizon (12 simulator steps of 5 s) heat leaks through
    # the walls at 12 * BASE_LEAK per degree of difference. The model should
    # find that number from the data alone.
    expected = 12 * connector.BASE_LEAK
    assert abs(coefficients["outdoor_gap"] - expected) / expected < 0.3


def test_works_without_window_and_light_history():
    # Before channel 2 is readable the history only has temperatures.
    bare = [{"t": s["t"], "zones": {z: {"temp": v["temp"]} for z, v in s["zones"].items()}}
            for s in simulate_history(hours=2)]
    info = new_forecaster().train_zone("livingroom", bare)
    assert info["usable"]
    assert info["coefficients"]["window_exchange"] == 0
    assert info["coefficients"]["sunlight"] == 0


def test_no_example_spans_a_gap_in_the_data():
    samples = simulate_history(hours=1)
    gapped = samples[:100] + [
        {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(forecaster.to_epoch(s["t"]) + 10 * 3600)),
         "zones": s["zones"]}
        for s in samples[100:]
    ]
    fc = new_forecaster()
    X_full, _ = fc.build_dataset(samples, "livingroom")
    X_gapped, _ = fc.build_dataset(gapped, "livingroom")
    assert len(X_full) - len(X_gapped) <= fc.cfg["horizon_steps"]


def test_timestamps_are_read_as_utc():
    assert forecaster.to_epoch("1970-01-01T00:01:00Z") == 60
