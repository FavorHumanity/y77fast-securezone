# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Loader that only returns verified, fresh, well-formed zone maps."""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .signing import matching_key_id

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_MAX_AGE = timedelta(days=30)

# Input size and geometry limits (reject early and loudly; a map is small by design).
MAX_FILE_BYTES = 1_048_576          # 1 MiB per map or key file
MAX_ZONES = 100
MAX_RING_POINTS = 100
MAX_WINDOWS_PER_DAY = 8
MAX_ID_LEN = 64
MAX_ZONE_EXTENT_DEG = 0.5           # bounding box of one zone; also rejects polygons that wrap the antimeridian
MAX_VALIDITY = timedelta(days=400)  # expires_at - issued_at
MAX_SEQUENCE = 2**53


class MapRejected(Exception):
    """Raised when a map file must not be used."""


def read_limited(path: str, limit: int = MAX_FILE_BYTES) -> bytes:
    """Read a file but refuse anything bigger than `limit` bytes (never loads an oversized file)."""
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise MapRejected(f"file larger than {limit} bytes")
    return data


def _ts(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MapRejected(f"bad timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        raise MapRejected(f"timestamp without timezone: {value}")
    return dt


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(a, b, c) -> bool:
    return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])


def _segments_touch(p1, p2, p3, p4) -> bool:
    d1, d2, d3, d4 = _orient(p3, p4, p1), _orient(p3, p4, p2), _orient(p1, p2, p3), _orient(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0 and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0:
        return True
    return ((d1 == 0 and _on_seg(p3, p4, p1)) or (d2 == 0 and _on_seg(p3, p4, p2))
            or (d3 == 0 and _on_seg(p1, p2, p3)) or (d4 == 0 and _on_seg(p1, p2, p4)))


def _check_ring(ring) -> None:
    """Geometry limits for the single outer ring: size, finite in-range coords, extent, area, no self-crossing."""
    if not isinstance(ring, list) or not 4 <= len(ring) <= MAX_RING_POINTS:
        raise MapRejected(f"polygon ring must have 4 to {MAX_RING_POINTS} points")
    if ring[0] != ring[-1]:
        raise MapRejected("polygon ring too short or not closed")
    for pt in ring:
        if (not isinstance(pt, list) or len(pt) < 2
                or not all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c)
                           for c in pt[:2])
                or not (-180 <= pt[0] <= 180 and -90 <= pt[1] <= 90)):
            raise MapRejected("bad polygon coordinate")
    xs, ys = [pt[0] for pt in ring], [pt[1] for pt in ring]
    if max(xs) - min(xs) > MAX_ZONE_EXTENT_DEG or max(ys) - min(ys) > MAX_ZONE_EXTENT_DEG:
        raise MapRejected("zone too large (or wraps the antimeridian)")
    if max(abs(y) for y in ys) > 85:
        raise MapRejected("zone too close to a pole for the flat-distance approximation")
    area2 = sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1))
    if area2 == 0:
        raise MapRejected("zone polygon has zero area")
    n = len(ring) - 1  # edges 0..n-1; edge i joins ring[i], ring[i+1]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges share a vertex by design
            if _segments_touch(ring[i], ring[i + 1], ring[j], ring[j + 1]):
                raise MapRejected("zone polygon crosses or touches itself")


def validate_schema(payload: dict) -> None:
    """Raise MapRejected (never another exception type) for any malformed payload."""
    try:
        _validate_schema(payload)
    except MapRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError) as exc:
        raise MapRejected(f"malformed map: {type(exc).__name__}") from exc


def _validate_meta(meta: dict) -> None:
    for k in ("map_version", "issued_at", "expires_at"):
        if k not in meta:
            raise MapRejected(f"metadata missing {k}")
    mv = meta["map_version"]
    if not isinstance(mv, str) or not 0 < len(mv) <= MAX_ID_LEN:
        raise MapRejected("map_version must be a short non-empty string")
    issued, expires = _ts(meta["issued_at"]), _ts(meta["expires_at"])
    if expires <= issued:
        raise MapRejected("expires_at must be after issued_at")
    if expires - issued > MAX_VALIDITY:
        raise MapRejected("map validity period too long")
    if "sequence" in meta:
        seq = meta["sequence"]
        if isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= MAX_SEQUENCE:
            raise MapRejected("sequence must be an integer >= 1")


def _validate_schema(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise MapRejected("payload is not an object")
    if payload.get("type") != "FeatureCollection":
        raise MapRejected("payload is not a GeoJSON FeatureCollection")
    meta = payload.get("metadata")
    if not isinstance(meta, dict):
        raise MapRejected("missing metadata")
    _validate_meta(meta)
    feats = payload.get("features")
    if not isinstance(feats, list) or not feats:
        raise MapRejected("no zones")
    if len(feats) > MAX_ZONES:
        raise MapRejected(f"too many zones (max {MAX_ZONES})")
    seen = set()
    for f in feats:
        if not isinstance(f, dict) or not isinstance(f.get("properties"), dict) \
                or not isinstance(f.get("geometry"), dict):
            raise MapRejected("zone must be an object with properties and geometry")
        p, g = f["properties"], f["geometry"]
        if g.get("type") != "Polygon":
            raise MapRejected("zone geometry must be a Polygon (MultiPolygon is not supported)")
        rings = g.get("coordinates")
        if not isinstance(rings, list) or not rings:
            raise MapRejected("zone geometry must be a Polygon")
        if len(rings) != 1:
            raise MapRejected("polygon holes (inner rings) are not supported; split the zone instead")
        _check_ring(rings[0])
        for k in ("zone_id", "speed_limit_mph", "timezone", "active_hours"):
            if k not in p:
                raise MapRejected(f"zone missing {k}")
        zid = p["zone_id"]
        if not isinstance(zid, str) or not 0 < len(zid) <= MAX_ID_LEN:
            raise MapRejected("zone_id must be a short non-empty string")
        if zid in seen:
            raise MapRejected(f"duplicate zone_id {zid}")
        seen.add(zid)
        if not isinstance(p["active_hours"], dict):
            raise MapRejected("active_hours must be an object")
        lim = p["speed_limit_mph"]
        if isinstance(lim, bool) or not isinstance(lim, (int, float)) or not (0 < lim <= 45):
            raise MapRejected("implausible school-zone speed limit")
        try:
            ZoneInfo(p["timezone"])
        except Exception as exc:
            raise MapRejected(f"bad timezone {p['timezone']}") from exc
        for day, windows in p["active_hours"].items():
            if day not in DAYS:
                raise MapRejected(f"bad weekday {day}")
            if not isinstance(windows, list) or len(windows) > MAX_WINDOWS_PER_DAY:
                raise MapRejected(f"active windows must be a list of at most {MAX_WINDOWS_PER_DAY}")
            for w in windows:
                if not isinstance(w, list) or len(w) != 2:
                    raise MapRejected("each active window must be [start, end]")
                start, end = w
                # "24:00" is allowed as an end only, so a window that crosses midnight is written as two
                # windows (for example sat 22:00-24:00 and sun 00:00-02:00).
                if not (isinstance(start, str) and isinstance(end, str)
                        and HHMM.match(start) and (HHMM.match(end) or end == "24:00")):
                    raise MapRejected("active window times must be zero-padded HH:MM (end may be 24:00)")
                if not start < end:
                    raise MapRejected("active window start must be before end")


def load_verified(envelope: dict, pub_pem, now: datetime | None = None,
                  max_age: timedelta = DEFAULT_MAX_AGE, guard=None) -> dict:
    """Return the payload or raise MapRejected (unsigned, tampered, expired, stale, invalid, rolled back).

    `pub_pem` is one PEM key or a list of PEM keys (rotation). `guard` is an optional RollbackGuard
    (read-only check here; call guard.accept(payload) to remember a map).
    """
    if not isinstance(envelope, dict) or "signature" not in envelope or "payload" not in envelope:
        raise MapRejected("unsigned map file")
    if envelope.get("alg") != "Ed25519":
        raise MapRejected("unsupported signature algorithm")
    pl = envelope["payload"]
    if isinstance(pl, dict) and isinstance(pl.get("features"), list) and len(pl["features"]) > MAX_ZONES:
        raise MapRejected(f"too many zones (max {MAX_ZONES})")  # cheap check before any hashing work
    kid = matching_key_id(envelope, pub_pem)
    if kid is None:
        raise MapRejected("signature invalid (tampered or wrong key)")
    payload = envelope["payload"]
    validate_schema(payload)
    now = now or datetime.now(timezone.utc)
    meta = payload["metadata"]
    issued, expires = _ts(meta["issued_at"]), _ts(meta["expires_at"])
    if issued > now:
        raise MapRejected("map issued in the future")
    if now >= expires:
        raise MapRejected("map expired")
    if now - issued > max_age:
        raise MapRejected("map is stale")
    if guard is not None:
        guard.check(payload, key_id=kid)
    return payload


def load_file(path: str, pub_pem, **kw) -> dict:
    try:
        raw = read_limited(path)
        envelope = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise MapRejected("not valid JSON") from exc
    return load_verified(envelope, pub_pem, **kw)
