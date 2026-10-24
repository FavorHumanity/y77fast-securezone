# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Loader that only returns verified, fresh, well-formed zone maps."""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .signing import verify_signature

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_MAX_AGE = timedelta(days=30)


class MapRejected(Exception):
    """Raised when a map file must not be used."""


def _ts(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MapRejected(f"bad timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        raise MapRejected(f"timestamp without timezone: {value}")
    return dt


def validate_schema(payload: dict) -> None:
    """Raise MapRejected (never another exception type) for any malformed payload."""
    try:
        _validate_schema(payload)
    except MapRejected:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        raise MapRejected(f"malformed map: {type(exc).__name__}") from exc


def _validate_schema(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise MapRejected("payload is not an object")
    if payload.get("type") != "FeatureCollection":
        raise MapRejected("payload is not a GeoJSON FeatureCollection")
    meta = payload.get("metadata")
    if not isinstance(meta, dict):
        raise MapRejected("missing metadata")
    for k in ("map_version", "issued_at", "expires_at"):
        if k not in meta:
            raise MapRejected(f"metadata missing {k}")
    feats = payload.get("features")
    if not isinstance(feats, list) or not feats:
        raise MapRejected("no zones")
    for f in feats:
        if not isinstance(f, dict) or not isinstance(f.get("properties"), dict) \
                or not isinstance(f.get("geometry"), dict):
            raise MapRejected("zone must be an object with properties and geometry")
        p, g = f["properties"], f["geometry"]
        if g.get("type") != "Polygon" or not g.get("coordinates"):
            raise MapRejected("zone geometry must be a Polygon")
        ring = g["coordinates"][0]
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise MapRejected("polygon ring too short or not closed")
        for pt in ring:
            if (len(pt) < 2 or not all(isinstance(c, (int, float)) and not isinstance(c, bool)
                                       and math.isfinite(c) for c in pt[:2])
                    or not (-180 <= pt[0] <= 180 and -90 <= pt[1] <= 90)):
                raise MapRejected("bad polygon coordinate")
        for k in ("zone_id", "speed_limit_mph", "timezone", "active_hours"):
            if k not in p:
                raise MapRejected(f"zone missing {k}")
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
            for start, end in windows:
                if not (isinstance(start, str) and isinstance(end, str)
                        and HHMM.match(start) and HHMM.match(end)):
                    raise MapRejected("active window times must be zero-padded HH:MM (00:00-23:59)")
                if not start < end:
                    raise MapRejected("active window start must be before end")


def load_verified(envelope: dict, pub_pem: bytes, now: datetime | None = None,
                  max_age: timedelta = DEFAULT_MAX_AGE) -> dict:
    """Return the payload or raise MapRejected (unsigned, tampered, expired, stale, invalid)."""
    if not isinstance(envelope, dict) or "signature" not in envelope or "payload" not in envelope:
        raise MapRejected("unsigned map file")
    if envelope.get("alg") != "Ed25519":
        raise MapRejected("unsupported signature algorithm")
    if not verify_signature(envelope, pub_pem):
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
    return payload


def load_file(path: str, pub_pem: bytes, **kw) -> dict:
    with open(path, encoding="utf-8") as fh:
        try:
            envelope = json.load(fh)
        except json.JSONDecodeError as exc:
            raise MapRejected("not valid JSON") from exc
    return load_verified(envelope, pub_pem, **kw)
