# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Part 2: independent hard speed-cap supervisor. Simulation only.

Pure functions, no I/O, fixed rules. Sits between a (simulated) planner and actuator:

1. Never raises the commanded speed: output = min(commanded, cap). Never raises an exception.
2. Inside an active zone the cap is that zone's limit; overlapping zones use the lowest limit.
3. Approaching a zone that is active, or that opens within a look-ahead window, the cap ramps down at no
   more than `max_decel_mps2` (cap = sqrt(limit^2 + 2*a*d)), so the car is at the limit on entry. A car
   already inside a zone that opens soon gets a time ramp (limit + a*seconds_to_open), so it is at the limit
   when the window opens.
4. If the map is missing, unverified, expired, stale or rolled back, or the position, time or position
   history is not trustworthy, fall back to the lowest school limit (fail-safe), ramping down from the
   previous speed. With no valid map the "known area" must be configured: if it is not, the answer is
   fail-safe everywhere (it is never silently switched off).
5. Hard cap wins over comfort: if a jump in position or data forces a stronger brake than `max_decel_mps2`,
   the cap still applies and the decision says so (`comfort_exceeded`, `implied_decel_mps2`).

lat and lon are keyword-only everywhere, so (lat, lon) and (lon, lat) can never be mixed up.
"""

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import NamedTuple

from .loader import load_verified
from .zones import point_in_polygon, seconds_until_active, valid_position

MPH_TO_MPS = 0.44704
# Plan the approach ramp with a margin so 1 s control steps never brake harder than max_decel_mps2.
PLANNING_MARGIN = 0.8
M_PER_DEG_LAT = 111_320.0
EARTH_RADIUS_M = 6_371_000.0
# (min_lon, min_lat, max_lon, max_lat) around the FICTIONAL demo zones. For demos and tests only: it is NOT
# the default any more. Real use must derive the area from the map or configure it.
DEMO_KNOWN_AREA = (-97.80, 30.22, -97.70, 30.32)


class Position(NamedTuple):
    """A named (lat, lon) pair, for places where a bare tuple could be read in the wrong order."""
    lat: float
    lon: float


def _ok(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


@dataclass(frozen=True)
class SupervisorConfig:
    max_decel_mps2: float = 2.0          # comfortable braking, configurable
    fallback_limit_mph: float = 20.0     # lowest school limit for the known area
    # (min_lon, min_lat, max_lon, max_lat): area the zone map is supposed to cover. None = not configured,
    # which means "fail safe everywhere when there is no valid map" (never fail open).
    known_area: tuple | None = None
    lookahead_margin_s: float = 10.0     # D1: extra seconds of notice before a zone's window opens
    max_plausible_speed_mps: float = 90.0  # GPS plausibility: faster implied movement is a position glitch
    max_clock_skew_s: float | None = None  # trusted clock: None = simulation (no check); else max |when - wall|

    def __post_init__(self):
        # A bad config must be rejected loudly; it must never silently switch the fail-safe off.
        if not _ok(self.fallback_limit_mph) or not 0 < self.fallback_limit_mph <= 45:
            raise ValueError("fallback_limit_mph must be finite and in (0, 45]")
        if not _ok(self.max_decel_mps2) or self.max_decel_mps2 <= 0:
            raise ValueError("max_decel_mps2 must be finite and > 0")
        if not _ok(self.lookahead_margin_s) or self.lookahead_margin_s < 0:
            raise ValueError("lookahead_margin_s must be finite and >= 0")
        if not _ok(self.max_plausible_speed_mps) or self.max_plausible_speed_mps <= 0:
            raise ValueError("max_plausible_speed_mps must be finite and > 0")
        if self.max_clock_skew_s is not None and (not _ok(self.max_clock_skew_s) or self.max_clock_skew_s <= 0):
            raise ValueError("max_clock_skew_s must be None or finite and > 0")
        a = self.known_area
        if a is None:
            return
        if not (isinstance(a, (tuple, list)) and len(a) == 4 and all(_ok(v) for v in a)):
            raise ValueError("known_area must be 4 finite numbers (min_lon, min_lat, max_lon, max_lat)")
        if not (-180 <= a[0] < a[2] <= 180 and -90 <= a[1] < a[3] <= 90):
            raise ValueError("known_area must be ordered and within valid lon/lat ranges")


DEFAULT_CONFIG = SupervisorConfig()


def known_area_from_payload(payload: dict, margin_m: float = 2000.0) -> tuple:
    """Bounding box of every zone in a VERIFIED map, grown by `margin_m` metres (D4: map-derived area).

    Raises ValueError if the map's zones span the antimeridian or the box would be invalid; in that case
    configure known_area by hand.
    """
    if not _ok(margin_m) or margin_m < 0:
        raise ValueError("margin_m must be finite and >= 0")
    pts = [pt for f in payload["features"] for pt in f["geometry"]["coordinates"][0]]
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    if max(lons) - min(lons) > 180:
        raise ValueError("zones span the antimeridian; configure known_area manually")
    mid_lat = (max(lats) + min(lats)) / 2
    dlat = margin_m / M_PER_DEG_LAT
    dlon = margin_m / (M_PER_DEG_LAT * max(math.cos(math.radians(mid_lat)), 0.05))
    area = (max(-180.0, min(lons) - dlon), max(-90.0, min(lats) - dlat),
            min(180.0, max(lons) + dlon), min(90.0, max(lats) + dlat))
    return area


def config_for_map(payload: dict, config: SupervisorConfig = DEFAULT_CONFIG,
                   margin_m: float = 2000.0) -> SupervisorConfig:
    """Copy of `config` whose known_area comes from a verified map. Call at start-up after a good load and
    keep the result, so a later map failure still has a known area to fail safe inside."""
    return replace(config, known_area=known_area_from_payload(payload, margin_m))


@dataclass(frozen=True)
class CapDecision:
    speed_mph: float
    cap_mph: float | None
    reason: str
    zone_ids: tuple = field(default_factory=tuple)         # zones the car is inside that are active now
    lookahead_zone_ids: tuple = field(default_factory=tuple)  # zones driving a look-ahead ramp (not active yet)
    fail_safe: bool = False                                # True when the fail-safe fallback was applied
    implied_decel_mps2: float | None = None                # braking implied by prev speed -> output (if known)
    comfort_exceeded: bool = False                         # hard cap forced braking above max_decel_mps2


def _xy(lon: float, lat: float, lat0: float) -> tuple:
    return (lon * M_PER_DEG_LAT * math.cos(math.radians(lat0)), lat * M_PER_DEG_LAT)


def distance_to_zone_m(ring: list, *, lon: float, lat: float) -> float:
    """0 if inside or on the polygon, else shortest distance to its edge (metres, local flat approx)."""
    if point_in_polygon(ring, lon=lon, lat=lat):
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


def distance_between_m(a: Position, b: Position) -> float:
    """Great-circle (haversine) distance in metres."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b.lon - a.lon) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _ramp_cap_mph(limit_mph: float, dist_m: float, decel: float) -> float:
    v = limit_mph * MPH_TO_MPS
    return math.sqrt(v * v + 2.0 * decel * dist_m) / MPH_TO_MPS


def _time_ramp_cap_mph(limit_mph: float, seconds_to_open: float, decel: float) -> float:
    """Cap for a car already inside a zone that opens in `seconds_to_open`: limit + a*t, so it is at the
    limit exactly when the window opens."""
    return (limit_mph * MPH_TO_MPS + decel * seconds_to_open) / MPH_TO_MPS


def _in_known_area(lat: float, lon: float, area: tuple) -> bool:
    return area[0] <= lon <= area[2] and area[1] <= lat <= area[3]


def _finish(commanded: float, cap: float | None, reason: str, zone_ids: tuple, look_ids: tuple,
            fail_safe: bool, prev_speed: float | None, dt: float, config: SupervisorConfig) -> CapDecision:
    speed = commanded if cap is None else min(commanded, cap)
    decel = None
    if prev_speed is not None:
        decel = max(0.0, (prev_speed - speed) * MPH_TO_MPS / dt)
    return CapDecision(speed, cap, reason, zone_ids, look_ids, fail_safe, decel,
                       decel is not None and decel > config.max_decel_mps2 + 1e-9)


def _fail_safe(commanded: float, why: str, prev_speed: float | None, dt: float,
               config: SupervisorConfig) -> CapDecision:
    cap = config.fallback_limit_mph
    if prev_speed is not None:
        cap = max(cap, prev_speed - config.max_decel_mps2 * dt / MPH_TO_MPS)
    return _finish(commanded, cap, f"FAIL-SAFE: {why}, lowest school limit", (), (), True,
                   prev_speed, dt, config)


def cap_speed(commanded_mph: float, *, lat: float, lon: float, when: datetime, payload: dict | None,
              config: SupervisorConfig = DEFAULT_CONFIG, prev_speed_mph: float | None = None,
              dt_s: float = 1.0, prev_position: Position | None = None,
              fail_reason: str | None = None) -> CapDecision:
    """Return the capped speed. `payload` must come from load_verified(); pass None if it failed.

    `prev_position` (a Position) enables the GPS plausibility check. `fail_reason` forces the fail-safe
    with that reason (used by cap_from_envelope for an untrusted clock). Never raises.
    """
    try:
        commanded = float(commanded_mph)
    except (TypeError, ValueError):
        commanded = 0.0
    if not math.isfinite(commanded) or commanded < 0:
        commanded = 0.0  # NaN, infinite or negative command: treat as stop
    prev_speed, dt = prev_speed_mph, dt_s
    if not _ok(dt) or dt <= 0:
        prev_speed, dt = None, 1.0  # unusable step length: no ramp-from-previous, never a crash
    elif prev_speed is not None and (not _ok(prev_speed) or prev_speed < 0):
        prev_speed = None
    try:
        return _cap_speed(commanded, lat, lon, when, payload, config, prev_speed, dt, prev_position,
                          fail_reason)
    except Exception:  # noqa: BLE001  the supervisor must never raise; unknown trouble means fail-safe
        return _fail_safe(commanded, "internal error", prev_speed, dt, config)


def _cap_speed(commanded, lat, lon, when, payload, config, prev_speed, dt, prev_position, fail_reason):
    position_ok = valid_position(lat, lon)
    why = fail_reason
    if why is None and not position_ok:
        why = "position invalid"
    if why is None and not (isinstance(when, datetime) and when.tzinfo is not None):
        why = "time invalid"
    if why is None and prev_position is not None:
        pp = prev_position
        if not (isinstance(pp, Position) and valid_position(pp.lat, pp.lon)):
            why = "previous position invalid"
        elif distance_between_m(pp, Position(lat, lon)) / dt > config.max_plausible_speed_mps:
            why = "position jump implausible"
    if why is None and payload is None:
        area = config.known_area
        if area is not None and not _in_known_area(lat, lon, area):
            return _finish(commanded, None, "no map, outside known area", (), (), False, prev_speed, dt, config)
        why = "map unavailable" if area is not None else "map unavailable and no known area configured"
    if why is not None:
        return _fail_safe(commanded, why, prev_speed, dt, config)

    a_plan = config.max_decel_mps2 * PLANNING_MARGIN
    v_now_mps = max(commanded, prev_speed or 0.0) * MPH_TO_MPS
    caps, inside, look = [], [], []   # caps holds (cap_mph, is_lookahead)
    for f in payload["features"]:
        p = f["properties"]
        limit = float(p["speed_limit_mph"])
        # D1: how far ahead to treat a not-yet-active zone as active = time to brake from the current speed
        # down to the limit, plus a margin.
        horizon = max(0.0, v_now_mps - limit * MPH_TO_MPS) / a_plan + config.lookahead_margin_s
        tau = seconds_until_active(p, when, horizon)
        if tau is None:
            continue
        d = distance_to_zone_m(f["geometry"]["coordinates"][0], lon=lon, lat=lat)
        cap = _ramp_cap_mph(limit, d, a_plan)
        if tau == 0.0:
            if d == 0.0:
                inside.append(p["zone_id"])
        else:
            look.append(p["zone_id"])
            if d == 0.0:
                # already inside a zone that opens in tau seconds: ramp by time (the distance ramp would be
                # the bare limit here, which is exactly the one-step drop D1 is about)
                cap = _time_ramp_cap_mph(limit, tau, a_plan)
        caps.append((cap, tau != 0.0))
    if not caps:
        return _finish(commanded, None, "no active zone", (), (), False, prev_speed, dt, config)
    cap, from_lookahead = min(caps)
    if inside:
        reason = "in active zone"
    elif cap < commanded:
        reason = "approaching zone opening: look-ahead ramp" if from_lookahead else "near active zone: ramped cap"
    else:
        return _finish(commanded, None, "no active zone nearby", (), (), False, prev_speed, dt, config)
    return _finish(commanded, cap, reason, tuple(inside), tuple(look), False, prev_speed, dt, config)


def cap_from_envelope(commanded_mph: float, *, lat: float, lon: float, when: datetime, envelope: dict | None,
                      pub_pem, config: SupervisorConfig = DEFAULT_CONFIG,
                      prev_speed_mph: float | None = None, dt_s: float = 1.0,
                      prev_position: Position | None = None, wall_now: datetime | None = None,
                      guard=None) -> CapDecision:
    """Verify the signed map, then cap. Any rejection triggers the fail-safe.

    Trusted clock: if config.max_clock_skew_s is set, `when` must be within that many seconds of the
    machine's own clock (`wall_now`, default the system clock), otherwise fail-safe ("clock untrusted").
    A caller-supplied time can otherwise make an old map look fresh or a zone look inactive.
    `guard` is an optional RollbackGuard that rejects replayed (older) maps.
    """
    fail_reason = None
    payload = None
    try:
        if when.tzinfo is None:
            raise ValueError("naive datetime")
        if config.max_clock_skew_s is not None:
            ref = wall_now or datetime.now(timezone.utc)
            if abs((when - ref).total_seconds()) > config.max_clock_skew_s:
                fail_reason = "clock untrusted"
        if fail_reason is None and envelope is not None:
            payload = load_verified(envelope, pub_pem, now=when, guard=guard)
    except Exception:  # noqa: BLE001  any failure while loading the map must fail safe, not crash
        payload = None
    return cap_speed(commanded_mph, lat=lat, lon=lon, when=when, payload=payload, config=config,
                     prev_speed_mph=prev_speed_mph, dt_s=dt_s, prev_position=prev_position,
                     fail_reason=fail_reason)
