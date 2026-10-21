import time

from helpers import load

report = load("telegram_bot", "report.py")

COMFORT = {"temp_min": 22.0, "temp_max": 26.0, "hum_min": 30.0, "hum_max": 65.0}
NOW = 1_780_000_000


def sample(seconds_ago, temp, window=None, hum=50.0):
    room = {"temp": temp, "hum": hum}
    if window is not None:
        room["window"] = window
    return {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - seconds_ago)),
            "zones": {"livingroom": room, "outdoor": {"temp": 15.0}}}


def test_comfort_share_and_temperature_range():
    samples = [sample(400, 21.0), sample(300, 23.0), sample(200, 25.0), sample(100, 27.0)]
    temp = report.ComfortReport(samples, COMFORT, hours=1, now=NOW).temperature("livingroom")
    assert temp["comfort"] == 50
    assert (temp["min"], temp["max"], temp["avg"]) == (21.0, 27.0, 24.0)


def test_only_the_last_hours_are_counted():
    samples = [sample(3 * 3600, 30.0), sample(60, 24.0)]
    r = report.ComfortReport(samples, COMFORT, hours=1, now=NOW)
    assert len(r.samples) == 1
    assert r.temperature("livingroom")["comfort"] == 100


def test_window_open_time_and_openings():
    # One opening in steps (0 -> 20 -> 40 -> 0), then a second one.
    positions = [0, 20, 40, 0, 0, 60]
    samples = [sample(600 - i * 60, 24.0, window=p) for i, p in enumerate(positions)]
    win = report.ComfortReport(samples, COMFORT, hours=1, now=NOW).window("livingroom")
    assert win == {"open": 50, "openings": 2}


def test_missing_data_gives_nothing_instead_of_an_error():
    r = report.ComfortReport([sample(60, 24.0)], COMFORT, hours=1, now=NOW)
    assert r.window("livingroom") is None
    assert r.temperature("bedroom1") is None
