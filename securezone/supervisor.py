# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Part 2: independent hard speed-cap supervisor. Simulation only.

Pure functions, no I/O, fixed rules. Sits between a (simulated) planner and actuator:

1. Never raises the commanded speed: output = min(commanded, cap).
2. Inside an active zone the cap is that zone's limit; overlapping zones use the lowest limit.
3. Approaching an active zone, the cap ramps down at no more than `max_decel_mps2`
   (cap = sqrt(limit^2 + 2*a*d)), so the car is already at the limit on entry with no sudden stop.
4. If the map is missing, unverified, expired or stale and the car is inside the known area,
   fall back to the lowest school limit (fail-safe), ramping down from the previous speed.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

from .loader import MapRejected, load_verified
from .zones import is_active, point_in_polygon

MPH_TO_MPS = 0.44704
# Plan the approach ramp with a margin so 1 s control steps never brake harder than max_decel_mps2.
PLANNING_MARGIN = 0.8
M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class SupervisorConfig:
    max_decel_mps2: float = 2.0          # comfortable braking, configurable
    fallback_limit_mph: float = 20.0     # lowest school limit for the known area
    # (min_lon, min_lat, max_lon, max_lat): area the zone map is supposed to cover (sample area)
    known_area: tuple = (-97.80, 30.22, -97.70, 30.32)


DEFAULT_CONFIG = SupervisorConfig()


@dataclass(frozen=True)
class CapDecision:
    speed_mph: float
    cap_mph: float | None
    reason: str
    zone_ids: tuple = field(default_factory=tuple)


def _xy(lon: float, lat: float, lat0: float) -> tuple:
    return (lon * M_PER_DEG_LAT * math.cos(math.radians(lat0)), lat * M_PER_DEG_LAT)


def distance_to_zone_m(lon: float, lat: float, ring: list) -> float:
    """0 if inside or on the polygon, else shortest distance to its edge (metres, local flat approx)."""
    if point_in_polygon(lon, lat, ring):
        return 0.0
    px, py = _xy(lon, lat, lat)
    best = math.inf
    for i in range(len(ring) - 1):
        ax, ay = _xy(ring[i][0], ring[i][1], lat)
        bx, by = _xy(ring[i + 1][0], ring[i + 1][1], lat)
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = 0.0 if seg == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def _ramp_cap_mph(limit_mph: float, dist_m: float, decel: float) -> float:
    v = limit_mph * MPH_TO_MPS
    return math.sqrt(v * v + 2.0 * decel * dist_m) / MPH_TO_MPS


def _in_known_area(lat: float, lon: float, area: tuple) -> bool:
    return area[0] <= lon <= area[2] and area[1] <= lat <= area[3]


def cap_speed(commanded_mph: float, lat: float, lon: float, when: datetime, payload: dict | None,
              config: SupervisorConfig = DEFAULT_CONFIG, prev_speed_mph: float | None = None,
              dt_s: float = 1.0) -> CapDecision:
    """Return the capped speed. `payload` must come from load_verified(); pass None if it failed."""
    commanded = max(0.0, float(commanded_mph))
    if payload is None:
        if not _in_known_area(lat, lon, config.known_area):
            return CapDecision(commanded, None, "no map, outside known area")
        cap = config.fallback_limit_mph
        if prev_speed_mph is not None:
            ramp = prev_speed_mph - config.max_decel_mps2 * dt_s / MPH_TO_MPS
            cap = max(cap, ramp)
        return CapDecision(min(commanded, cap), cap, "FAIL-SAFE: map unavailable, lowest school limit")

    caps, inside = [], []
    for f in payload["features"]:
        p = f["properties"]
        if not is_active(p, when):
            continue
        d = distance_to_zone_m(lon, lat, f["geometry"]["coordinates"][0])
        limit = float(p["speed_limit_mph"])
        if d == 0.0:
            inside.append(p["zone_id"])
        caps.append(_ramp_cap_mph(limit, d, config.max_decel_mps2 * PLANNING_MARGIN))
    if not caps:
        return CapDecision(commanded, None, "no active zone")
    cap = min(caps)
    if inside:
        reason = "in active zone"
    elif cap < commanded:
        reason = "near active zone: ramped cap"
    else:
        reason = "no active zone nearby"
        return CapDecision(commanded, None, reason)
    return CapDecision(min(commanded, cap), cap, reason, tuple(inside))


def cap_from_envelope(commanded_mph: float, lat: float, lon: float, when: datetime, envelope: dict | None,
                      pub_pem: bytes, config: SupervisorConfig = DEFAULT_CONFIG,
                      prev_speed_mph: float | None = None, dt_s: float = 1.0) -> CapDecision:
    """Verify the signed map, then cap. Any rejection triggers the fail-safe."""
    try:
        payload = load_verified(envelope, pub_pem, now=when) if envelope is not None else None
    except MapRejected:
        payload = None
    return cap_speed(commanded_mph, lat, lon, when, payload, config, prev_speed_mph, dt_s)
