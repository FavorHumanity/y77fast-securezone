# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Part 2 tests: hard speed-cap supervisor (simulation only)."""

import copy
import io
import json
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from securezone.cli import main
from securezone.signing import generate_demo_keypair, sign_map
from securezone.supervisor import (
    MPH_TO_MPS,
    SupervisorConfig,
    cap_from_envelope,
    cap_speed,
    distance_to_zone_m,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "data", "sample_zones.geojson")
CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 21, 7, 30, tzinfo=CT)      # Wednesday, active
NOON = datetime(2026, 10, 21, 12, 0, tzinfo=CT)     # Wednesday, inactive
IN_LAT, IN_LON = 30.2610, -97.7590                  # inside SZ-DEMO-001 (20 mph)
FAR_LAT, FAR_LON = 30.2300, -97.7900                # in known area, far from zones
CFG = SupervisorConfig()


def sample():
    with open(SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


class SupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.priv, cls.pub = generate_demo_keypair()
        cls.payload = sample()
        cls.env = sign_map(cls.payload, cls.priv)

    def test_inside_active_zone_clamped(self):
        d = cap_speed(35, IN_LAT, IN_LON, NOW, self.payload)
        self.assertEqual(d.speed_mph, 20)
        self.assertEqual(d.zone_ids, ("SZ-DEMO-001",))

    def test_outside_not_clamped(self):
        self.assertEqual(cap_speed(35, FAR_LAT, FAR_LON, NOW, self.payload).speed_mph, 35)

    def test_inactive_hours_not_clamped(self):
        self.assertEqual(cap_speed(35, IN_LAT, IN_LON, NOON, self.payload).speed_mph, 35)

    def test_weekend_not_clamped(self):
        sat = datetime(2026, 10, 24, 7, 30, tzinfo=CT)
        self.assertEqual(cap_speed(35, IN_LAT, IN_LON, sat, self.payload).speed_mph, 35)

    def test_missing_map_fallback(self):
        d = cap_from_envelope(35, FAR_LAT, FAR_LON, NOW, None, self.pub)
        self.assertEqual(d.speed_mph, 20)
        self.assertTrue(d.reason.startswith("FAIL-SAFE"))

    def test_tampered_map_fallback(self):
        bad = copy.deepcopy(self.env)
        bad["payload"]["features"][0]["properties"]["speed_limit_mph"] = 45
        d = cap_from_envelope(35, IN_LAT, IN_LON, NOW, bad, self.pub)
        self.assertEqual(d.speed_mph, 20)
        self.assertTrue(d.reason.startswith("FAIL-SAFE"))

    def test_wrong_key_and_expired_fallback(self):
        _, other_pub = generate_demo_keypair()
        self.assertEqual(cap_from_envelope(35, IN_LAT, IN_LON, NOW, self.env, other_pub).speed_mph, 20)
        late = datetime(2028, 1, 5, 7, 30, tzinfo=CT)
        self.assertEqual(cap_from_envelope(35, IN_LAT, IN_LON, late, self.env, self.pub).speed_mph, 20)

    def test_stale_map_fallback_even_at_noon(self):
        stale = datetime(2026, 12, 2, 12, 0, tzinfo=CT)  # > 30 days after issue
        self.assertEqual(cap_from_envelope(35, IN_LAT, IN_LON, stale, self.env, self.pub).speed_mph, 20)

    def test_no_map_outside_known_area_not_capped(self):
        self.assertEqual(cap_from_envelope(35, 40.0, -100.0, NOW, None, self.pub).speed_mph, 35)

    def test_valid_envelope_used(self):
        self.assertEqual(cap_from_envelope(35, IN_LAT, IN_LON, NOW, self.env, self.pub).speed_mph, 20)

    def test_never_increases_speed_random(self):
        rng = random.Random(77)
        for _ in range(10_000):
            cmd = rng.uniform(0, 80)
            lat, lon = rng.uniform(30.255, 30.287), rng.uniform(-97.765, -97.735)
            when = NOW + timedelta(minutes=rng.randint(-600, 600))
            pl = self.payload if rng.random() < 0.8 else None
            prev = rng.choice([None, rng.uniform(0, 80)])
            d = cap_speed(cmd, lat, lon, when, pl, prev_speed_mph=prev)
            self.assertLessEqual(d.speed_mph, cmd + 1e-9)
            self.assertGreaterEqual(d.speed_mph, 0)
            if pl is not None and d.zone_ids:
                self.assertLessEqual(d.speed_mph, 25 + 1e-9)

    def test_low_command_unchanged_in_zone(self):
        self.assertEqual(cap_speed(12, IN_LAT, IN_LON, NOW, self.payload).speed_mph, 12)

    def test_deceleration_limit_on_approach(self):
        # Drive north into SZ-DEMO-001 from 300 m south at 1 s steps; no step may brake harder than max.
        lat, lon, speed, dt = 30.2600 - 300 / 111_320.0, -97.7590, 35.0, 1.0
        entered = False
        for _ in range(60):
            d = cap_speed(35, lat, lon, NOW, self.payload)
            drop_mps = (speed - d.speed_mph) * MPH_TO_MPS
            self.assertLessEqual(drop_mps, CFG.max_decel_mps2 * dt + 0.05)
            speed = d.speed_mph
            if d.zone_ids:
                entered = True
                self.assertLessEqual(speed, 20 + 1e-9)  # cap still applies on entry
                break
            lat += speed * MPH_TO_MPS * dt / 111_320.0
        self.assertTrue(entered)

    def test_fallback_ramp_respects_decel(self):
        cfg = SupervisorConfig(max_decel_mps2=2.0)
        d = cap_speed(40, FAR_LAT, FAR_LON, NOW, None, cfg, prev_speed_mph=40, dt_s=1.0)
        self.assertAlmostEqual((40 - d.speed_mph) * MPH_TO_MPS, 2.0, places=6)
        speed = 40.0
        for _ in range(30):
            speed = cap_speed(40, FAR_LAT, FAR_LON, NOW, None, cfg, prev_speed_mph=speed).speed_mph
        self.assertEqual(speed, 20)

    def test_boundary_on_edge_and_just_outside(self):
        ring = self.payload["features"][0]["geometry"]["coordinates"][0]
        self.assertEqual(distance_to_zone_m(-97.759, 30.2610, ring), 0.0)
        just_out = cap_speed(35, 30.2600 - 1e-6, -97.7590, NOW, self.payload)  # ~0.1 m south
        self.assertLess(just_out.speed_mph, 20.5)
        self.assertEqual(just_out.zone_ids, ())

    def test_boundary_active_window_edges(self):
        start = datetime(2026, 10, 21, 7, 0, tzinfo=CT)
        end = datetime(2026, 10, 21, 9, 0, tzinfo=CT)
        self.assertEqual(cap_speed(35, IN_LAT, IN_LON, start, self.payload).speed_mph, 20)
        self.assertEqual(cap_speed(35, IN_LAT, IN_LON, end, self.payload).speed_mph, 35)

    def test_overlapping_zones_lowest_limit(self):
        pl = copy.deepcopy(self.payload)
        extra = copy.deepcopy(pl["features"][0])
        extra["properties"]["zone_id"] = "SZ-DEMO-OVERLAP"
        extra["properties"]["speed_limit_mph"] = 15
        extra["geometry"]["coordinates"] = [[[-97.7595, 30.2605], [-97.7585, 30.2605],
                                             [-97.7585, 30.2615], [-97.7595, 30.2615],
                                             [-97.7595, 30.2605]]]
        pl["features"].append(extra)
        d = cap_speed(35, IN_LAT, IN_LON, NOW, pl)
        self.assertEqual(d.speed_mph, 15)
        self.assertEqual(set(d.zone_ids), {"SZ-DEMO-001", "SZ-DEMO-OVERLAP"})

    def test_deterministic(self):
        a = cap_speed(33, 30.2595, -97.7590, NOW, self.payload)
        b = cap_speed(33, 30.2595, -97.7590, NOW, self.payload)
        self.assertEqual(a, b)

    def test_cli_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp, pp = os.path.join(tmp, "s.json"), os.path.join(tmp, "p.pem")
            with open(sp, "w", encoding="utf-8") as fh:
                json.dump(self.env, fh)
            with open(pp, "wb") as fh:
                fh.write(self.pub)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["cap", sp, "--pub", pp, "--speed", "35", "--lat", str(IN_LAT),
                           "--lon", str(IN_LON), "--time", NOW.isoformat()])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue())["speed_mph"], 20)


if __name__ == "__main__":
    unittest.main()
