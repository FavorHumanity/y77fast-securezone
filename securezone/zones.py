# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Point-in-zone and active-hours checks."""

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .loader import DAYS


def valid_position(lat, lon) -> bool:
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and not isinstance(lat, bool) and not isinstance(lon, bool)
            and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)


def point_in_polygon(ring: list, *, lon: float, lat: float) -> bool:
    """Ray-casting test; ring is a list of [lon, lat]. lon and lat are keyword-only (no order mix-ups)."""
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


def seconds_until_active(props: dict, when: datetime, horizon_s: float) -> float | None:
    """0.0 if the zone is active now; else seconds until its next window opens if that is within
    `horizon_s`; else None. Looks at today and tomorrow in the zone's own timezone (DST-safe)."""
    if is_active(props, when):
        return 0.0
    tz = ZoneInfo(props["timezone"])
    now_utc = when.astimezone(timezone.utc)
    local_day = when.astimezone(tz).date()
    best = None
    for offset in (0, 1):
        day = local_day + timedelta(days=offset)
        for start, _end in props["active_hours"].get(DAYS[day.weekday()], []):
            opens = datetime(day.year, day.month, day.day, int(start[:2]), int(start[3:]), tzinfo=tz)
            delta = (opens.astimezone(timezone.utc) - now_utc).total_seconds()
            if 0 < delta <= horizon_s and (best is None or delta < best):
                best = delta
    return best


def check(payload: dict, *, lat: float, lon: float, when: datetime) -> dict:
    """Return zone status for a position and aware datetime (lat and lon are keyword-only).

    Raises ValueError for an invalid position (NaN, infinite, out of range): never answer "not in a zone"
    for a position that is not a position.

    Overlapping zones: an active zone wins over an inactive one, and the lowest limit wins
    among active zones (same rule as the supervisor).
    """
    if not valid_position(lat, lon):
        raise ValueError("invalid position")
    hits = [f["properties"] for f in payload["features"]
            if point_in_polygon(f["geometry"]["coordinates"][0], lon=lon, lat=lat)]
    if not hits:
        return {"in_zone": False, "zone_id": None, "active": False, "speed_limit_mph": None}
    active = [p for p in hits if is_active(p, when)]
    if active:
        p = min(active, key=lambda q: float(q["speed_limit_mph"]))
        return {"in_zone": True, "zone_id": p["zone_id"], "active": True,
                "speed_limit_mph": p["speed_limit_mph"]}
    return {"in_zone": True, "zone_id": hits[0]["zone_id"], "active": False, "speed_limit_mph": None}
