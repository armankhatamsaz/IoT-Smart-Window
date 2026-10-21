import time
import calendar


def to_epoch(stamp):
    # The adaptor's timestamps are UTC, e.g. "2026-10-21T10:13:38Z".
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def percent(part, whole):
    return round(100 * part / whole) if whole else 0


class ComfortReport:
    """How the rooms did over the last hours, computed from the history that
    the adaptor serves (one snapshot of every zone per row)."""

    def __init__(self, samples, comfort, hours, now=None):
        start = (now or time.time()) - hours * 3600
        self.samples = [s for s in samples if to_epoch(s["t"]) >= start]
        self.comfort = comfort
        self.hours = hours

    def values(self, zone, name):
        return [s["zones"][zone][name] for s in self.samples
                if name in s["zones"].get(zone, {})]

    def temperature(self, zone):
        temps = self.values(zone, "temp")
        if not temps:
            return None
        low, high = self.comfort["temp_min"], self.comfort["temp_max"]
        return {
            "avg": sum(temps) / len(temps),
            "min": min(temps),
            "max": max(temps),
            "comfort": percent(sum(low <= t <= high for t in temps), len(temps)),
        }

    def humidity_ok(self, zone):
        hums = self.values(zone, "hum")
        if not hums:
            return None
        low, high = self.comfort["hum_min"], self.comfort["hum_max"]
        return percent(sum(low <= h <= high for h in hums), len(hums))

    def window(self, zone):
        positions = self.values(zone, "window")
        if not positions:
            return None
        return {
            "open": percent(sum(p > 0 for p in positions), len(positions)),
            # The window moves in small steps, so count openings, not steps.
            "openings": sum(a == 0 and b > 0 for a, b in zip(positions, positions[1:])),
        }
