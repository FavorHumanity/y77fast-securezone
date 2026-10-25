# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""CLI: keygen (demo), sign, verify, check, cap. Simulation only.

Exit codes: 0 ok | 1 map rejected (verify, check) | 2 bad input or refused action | 3 `cap` applied the
FAIL-SAFE (the JSON line is still printed). Scripts must not treat 3 as success.
"""

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timezone

from .loader import MapRejected, load_verified, read_limited, validate_schema
from .rollback import RollbackGuard
from .signing import generate_demo_keypair, sign_map
from .supervisor import SupervisorConfig, cap_from_envelope
from .zones import check

EXIT_OK, EXIT_REJECTED, EXIT_USAGE, EXIT_FAILSAFE = 0, 1, 2, 3
MAX_KEY_BYTES = 65_536
STRICT_CLOCK_SKEW_S = 300.0


def _read(path: str, limit: int = MAX_KEY_BYTES) -> bytes:
    return read_limited(path, limit)


def _private_key_too_open(path: str) -> bool:
    """POSIX only: True if group or others can access the private key file."""
    if os.name != "posix":
        return False
    return bool(stat.S_IMODE(os.stat(path).st_mode) & 0o077)


def _write_private(path: str, data: bytes) -> None:
    """Write a secret atomically: temp file created 0600, then renamed over the target (mode never loose)."""
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _when(a):
    """Resolve --time. Returns (datetime, None) or (None, error message)."""
    try:
        when = datetime.fromisoformat(a.time) if getattr(a, "time", None) else datetime.now(timezone.utc)
    except ValueError:
        return None, "--time must be ISO 8601 with a UTC offset"
    if when.tzinfo is None:
        return None, "--time needs a UTC offset"
    if (getattr(a, "strict_clock", False) and a.time
            and abs((when - datetime.now(timezone.utc)).total_seconds()) > STRICT_CLOCK_SKEW_S):
        return None, f"--time is more than {int(STRICT_CLOCK_SKEW_S)} s from the system clock (--strict-clock)"
    return when, None


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="securezone", description="SecureZone signed zone maps (simulation)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, lat_lon=False, state_help="rollback state file"):
        p.add_argument("signed")
        p.add_argument("--pub", required=True)
        p.add_argument("--time", help="ISO 8601 with offset; default now (simulation override)")
        p.add_argument("--strict-clock", action="store_true",
                       help="refuse a --time that is more than 300 s from the system clock")
        p.add_argument("--state", help=state_help)
        if lat_lon:
            p.add_argument("--lat", type=float, required=True)
            p.add_argument("--lon", type=float, required=True)

    k = sub.add_parser("keygen", help="DEMO ONLY keypair (never commit the private key)")
    k.add_argument("--out", default="demo_keys")
    k.add_argument("--force", action="store_true", help="overwrite existing demo keys")
    s = sub.add_parser("sign")
    s.add_argument("map")
    s.add_argument("--key", required=True)
    s.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    common(v, state_help="rollback state file: reject older maps, then remember this one")
    c = sub.add_parser("check")
    common(c, lat_lon=True, state_help="rollback state file (read only): reject older maps")
    cp = sub.add_parser("cap", help="cap a commanded speed (fail-safe if the map is rejected; exit 3 then)")
    common(cp, lat_lon=True, state_help="rollback state file (read only): reject older maps")
    cp.add_argument("--speed", type=float, required=True, help="commanded speed, mph")
    cp.add_argument("--known-area", type=float, nargs=4, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
                    help="area the map should cover; without it a missing map fails safe everywhere")
    return ap


def _keygen(a) -> int:
    os.makedirs(a.out, mode=0o700, exist_ok=True)
    priv_path = os.path.join(a.out, "demo_private.pem")
    pub_path = os.path.join(a.out, "demo_public.pem")
    if not a.force and (os.path.exists(priv_path) or os.path.exists(pub_path)):
        print(f"REFUSED: keys already exist in {a.out}/ (use --force to overwrite)", file=sys.stderr)
        return EXIT_USAGE
    priv, pub = generate_demo_keypair()
    _write_private(priv_path, priv)
    with open(pub_path, "wb") as fh:
        fh.write(pub)
    print(f"DEMO keypair written to {a.out}/ (git-ignored, demo only)")
    return EXIT_OK


def _sign(a) -> int:
    try:
        if _private_key_too_open(a.key):
            print(f"REFUSED: {a.key} is readable by group/others; run chmod 600 on it", file=sys.stderr)
            return EXIT_USAGE
        payload = json.loads(read_limited(a.map).decode("utf-8"))
        validate_schema(payload)  # never sign a map the loader would reject
        envelope = sign_map(payload, _read(a.key))
    except (OSError, MapRejected, ValueError, TypeError, RecursionError) as exc:
        print(f"REFUSED: cannot sign ({type(exc).__name__}: {exc})", file=sys.stderr)
        return EXIT_USAGE
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(envelope, fh, indent=2, ensure_ascii=False)
    print(f"signed -> {a.out}")
    return EXIT_OK


def _cap(a) -> int:
    when, err = _when(a)
    if err:
        print(f"REJECTED: {err}", file=sys.stderr)
        return EXIT_USAGE
    try:
        cfg = SupervisorConfig(known_area=tuple(a.known_area) if a.known_area else None,
                               max_clock_skew_s=STRICT_CLOCK_SKEW_S if a.strict_clock else None)
    except ValueError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        envelope = json.loads(read_limited(a.signed).decode("utf-8"))
    except (OSError, MapRejected, ValueError, RecursionError):
        envelope = None
    try:
        pub = _read(a.pub)
    except (OSError, MapRejected):
        pub, envelope = b"", None  # unreadable public key: cannot verify, so fail safe
    guard = None
    if a.state:
        try:
            guard = RollbackGuard(a.state)
        except MapRejected:
            envelope = None  # unreadable rollback state: fail closed
    d = cap_from_envelope(a.speed, lat=a.lat, lon=a.lon, when=when, envelope=envelope, pub_pem=pub,
                          config=cfg, guard=guard)
    print(json.dumps({"commanded_mph": a.speed, "speed_mph": round(d.speed_mph, 2),
                      "cap_mph": None if d.cap_mph is None else round(d.cap_mph, 2),
                      "reason": d.reason, "zone_ids": list(d.zone_ids), "fail_safe": d.fail_safe}))
    return EXIT_FAILSAFE if d.fail_safe else EXIT_OK


def _verify_or_check(a) -> int:
    when, err = _when(a)
    if err:
        print(f"REJECTED: {err}", file=sys.stderr)
        return EXIT_USAGE
    try:
        guard = RollbackGuard(a.state) if a.state else None
        try:
            envelope = json.loads(read_limited(a.signed).decode("utf-8"))
        except (ValueError, RecursionError) as exc:
            raise MapRejected("not valid JSON") from exc
        payload = load_verified(envelope, _read(a.pub), now=when, guard=guard)
        if a.cmd == "verify" and guard is not None:
            guard.accept(payload)
    except (MapRejected, OSError) as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return EXIT_REJECTED
    if a.cmd == "verify":
        print(f"OK: map {payload['metadata']['map_version']} verified")
        return EXIT_OK
    try:
        print(json.dumps(check(payload, lat=a.lat, lon=a.lon, when=when)))
    except ValueError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def main(argv=None) -> int:
    a = _build_parser().parse_args(argv)
    if a.cmd == "keygen":
        return _keygen(a)
    if a.cmd == "sign":
        return _sign(a)
    if a.cmd == "cap":
        return _cap(a)
    return _verify_or_check(a)


if __name__ == "__main__":
    sys.exit(main())
