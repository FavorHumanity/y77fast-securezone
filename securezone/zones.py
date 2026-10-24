# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Point-in-zone and active-hours checks."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .loader import DAYS


def point_in_polygon(lon: float, lat: float, ring: list) -> bool:
    """Ray-casting test; ring is a list of [lon, lat]."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][:2]
        x2, y2 = ring[(i + 1) % n][:2]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def is_active(props: dict, when: datetime) -> bool:
    local = when.astimezone(ZoneInfo(props["timezone"]))
    hhmm = local.strftime("%H:%M")
    windows = props["active_hours"].get(DAYS[local.weekday()], [])
    return any(start <= hhmm < end for start, end in windows)


def check(payload: dict, lat: float, lon: float, when: datetime) -> dict:
    """Return zone status for a position and aware datetime.

    Overlapping zones: an active zone wins over an inactive one, and the lowest limit wins
    among active zones (same rule as the supervisor).
    """
    hits = [f["properties"] for f in payload["features"]
            if point_in_polygon(lon, lat, f["geometry"]["coordinates"][0])]
    if not hits:
        return {"in_zone": False, "zone_id": None, "active": False, "speed_limit_mph": None}
    active = [p for p in hits if is_active(p, when)]
    if active:
        p = min(active, key=lambda q: float(q["speed_limit_mph"]))
        return {"in_zone": True, "zone_id": p["zone_id"], "active": True,
                "speed_limit_mph": p["speed_limit_mph"]}
    return {"in_zone": True, "zone_id": hits[0]["zone_id"], "active": False, "speed_limit_mph": None}
