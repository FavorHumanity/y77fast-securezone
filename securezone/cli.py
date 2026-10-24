# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""CLI: keygen (demo), sign, verify, check, cap. Simulation only."""

import argparse
import json
import sys
from datetime import datetime, timezone

from .loader import MapRejected, load_file
from .signing import generate_demo_keypair, sign_map
from .supervisor import cap_from_envelope
from .zones import check


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="securezone", description="SecureZone signed zone maps (simulation)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="DEMO ONLY keypair (never commit the private key)")
    k.add_argument("--out", default="demo_keys")
    k.add_argument("--force", action="store_true", help="overwrite existing demo keys")
    s = sub.add_parser("sign")
    s.add_argument("map")
    s.add_argument("--key", required=True)
    s.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("signed")
    v.add_argument("--pub", required=True)
    v.add_argument("--time", help="ISO 8601 with offset; default now")
    c = sub.add_parser("check")
    c.add_argument("signed")
    c.add_argument("--pub", required=True)
    c.add_argument("--lat", type=float, required=True)
    c.add_argument("--lon", type=float, required=True)
    c.add_argument("--time", help="ISO 8601 with offset; default now")
    cp = sub.add_parser("cap", help="Part 2: cap a commanded speed (fail-safe if map is rejected)")
    cp.add_argument("signed")
    cp.add_argument("--pub", required=True)
    cp.add_argument("--speed", type=float, required=True, help="commanded speed, mph")
    cp.add_argument("--lat", type=float, required=True)
    cp.add_argument("--lon", type=float, required=True)
    cp.add_argument("--time", help="ISO 8601 with offset; default now")
    a = ap.parse_args(argv)

    if a.cmd == "keygen":
        import os
        os.makedirs(a.out, exist_ok=True)
        priv_path = os.path.join(a.out, "demo_private.pem")
        pub_path = os.path.join(a.out, "demo_public.pem")
        if not a.force and (os.path.exists(priv_path) or os.path.exists(pub_path)):
            print(f"REFUSED: keys already exist in {a.out}/ (use --force to overwrite)", file=sys.stderr)
            return 2
        priv, pub = generate_demo_keypair()
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(priv_path, flags, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(priv)
        with open(pub_path, "wb") as fh:
            fh.write(pub)
        print(f"DEMO keypair written to {a.out}/ (git-ignored, demo only)")
        return 0
    if a.cmd == "sign":
        with open(a.map, encoding="utf-8") as fh:
            payload = json.load(fh)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(sign_map(payload, _read(a.key)), fh, indent=2, ensure_ascii=False)
        print(f"signed -> {a.out}")
        return 0
    if a.cmd == "cap":
        try:
            when = datetime.fromisoformat(a.time) if a.time else datetime.now(timezone.utc)
        except ValueError:
            print("REJECTED: --time must be ISO 8601 with a UTC offset", file=sys.stderr)
            return 2
        if when.tzinfo is None:
            print("REJECTED: --time needs a UTC offset", file=sys.stderr)
            return 2
        try:
            with open(a.signed, encoding="utf-8") as fh:
                envelope = json.load(fh)
        except (OSError, json.JSONDecodeError):
            envelope = None
        try:
            pub = _read(a.pub)
        except OSError:
            pub, envelope = b"", None  # unreadable public key: cannot verify, so fail safe
        d = cap_from_envelope(a.speed, a.lat, a.lon, when, envelope, pub)
        print(json.dumps({"commanded_mph": a.speed, "speed_mph": round(d.speed_mph, 2),
                          "cap_mph": None if d.cap_mph is None else round(d.cap_mph, 2),
                          "reason": d.reason, "zone_ids": list(d.zone_ids)}))
        return 0
    try:
        try:
            when = datetime.fromisoformat(a.time) if getattr(a, "time", None) else datetime.now(timezone.utc)
        except ValueError:
            print("REJECTED: --time must be ISO 8601 with a UTC offset", file=sys.stderr)
            return 2
        if when.tzinfo is None:
            print("REJECTED: --time needs a UTC offset", file=sys.stderr)
            return 2
        payload = load_file(a.signed, _read(a.pub), now=when)
    except MapRejected as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1
    if a.cmd == "verify":
        print(f"OK: map {payload['metadata']['map_version']} verified")
        return 0
    print(json.dumps(check(payload, a.lat, a.lon, when)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
