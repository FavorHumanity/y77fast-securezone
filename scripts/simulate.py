# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Drive a simulated car through the 3 SAMPLE zones and print a timeline. Simulation only."""

import json
import math
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securezone.signing import generate_demo_keypair, sign_map
from securezone.supervisor import M_PER_DEG_LAT, MPH_TO_MPS, cap_from_envelope

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(start: datetime, commanded_mph: float = 35.0, dt: float = 1.0, every: int = 20,
        tamper: bool = False) -> list:
    with open(os.path.join(ROOT, "data", "sample_zones.geojson"), encoding="utf-8") as fh:
        payload = json.load(fh)
    priv, pub = generate_demo_keypair()  # demo key, generated at runtime
    env = sign_map(payload, priv)
    if tamper:
        env["payload"]["features"][0]["properties"]["speed_limit_mph"] = 45
    lat, lon = 30.2585, -97.762            # diagonal street through all 3 sample zones
    end_lat, end_lon = 30.2845, -97.736
    east = (end_lon - lon) * math.cos(math.radians(lat))
    north = end_lat - lat
    norm = math.hypot(east, north)
    ux, uy = north / norm, east / norm
    speed, t, rows, last_reason = 0.0, 0, [], None
    when = start
    while lon < end_lon:
        d = cap_from_envelope(commanded_mph, lat, lon, when, env, pub, prev_speed_mph=speed, dt_s=dt)
        speed = d.speed_mph
        if t % every == 0 or d.reason != last_reason:
            rows.append((t, lat, lon, commanded_mph, speed, d.cap_mph, d.reason, d.zone_ids))
            last_reason = d.reason
        step_m = speed * MPH_TO_MPS * dt
        lat += step_m * ux / M_PER_DEG_LAT
        lon += step_m * uy / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
        t += 1
        when = start + timedelta(seconds=t)
    return rows


def main() -> int:
    start = datetime(2026, 10, 21, 7, 30, tzinfo=ZoneInfo("America/Chicago"))
    tamper = "--tamper" in sys.argv
    if "--inactive" in sys.argv:
        start = start.replace(hour=12)
    print(f"SecureZone Part 2 simulation (SAMPLE data, not vehicle software) start={start.isoformat()}"
          f"{' TAMPERED MAP' if tamper else ''}")
    print(f"{'t(s)':>5} {'cmd':>5} {'out':>6} {'cap':>6}  reason")
    for t, _lat, _lon, cmd, out, cap, reason, zones in run(start, tamper=tamper):
        cap_s = f"{cap:6.1f}" if cap is not None else "     -"
        print(f"{t:5d} {cmd:5.1f} {out:6.1f} {cap_s}  {reason} {' '.join(zones)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
