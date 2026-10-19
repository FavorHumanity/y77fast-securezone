# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Loader that only returns verified, fresh, well-formed zone maps."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .signing import verify_signature

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_MAX_AGE = timedelta(days=30)


class MapRejected(Exception):
    """Raised when a map file must not be used."""


def _ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise MapRejected(f"timestamp without timezone: {value}")
    return dt


def validate_schema(payload: dict) -> None:
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
        p, g = f.get("properties", {}), f.get("geometry", {})
        if g.get("type") != "Polygon" or not g.get("coordinates"):
            raise MapRejected("zone geometry must be a Polygon")
        if len(g["coordinates"][0]) < 4:
            raise MapRejected("polygon ring too short")
        for k in ("zone_id", "speed_limit_mph", "timezone", "active_hours"):
            if k not in p:
                raise MapRejected(f"zone missing {k}")
        if not (0 < float(p["speed_limit_mph"]) <= 45):
            raise MapRejected("implausible school-zone speed limit")
        try:
            ZoneInfo(p["timezone"])
        except Exception as exc:
            raise MapRejected(f"bad timezone {p['timezone']}") from exc
        for day, windows in p["active_hours"].items():
            if day not in DAYS:
                raise MapRejected(f"bad weekday {day}")
            for start, end in windows:
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
