# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Tests: valid, unsigned, tampered, wrong key, expired, stale, outside zone, inactive hours."""

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from safezone.cli import main
from safezone.loader import MapRejected, load_verified
from safezone.signing import generate_demo_keypair, sign_map
from safezone.zones import check

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "data", "sample_zones.geojson")
CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 21, 7, 30, tzinfo=CT)  # Wednesday, school arrival window
IN_LAT, IN_LON = 30.2610, -97.7590  # inside SZ-DEMO-001


def sample():
    with open(SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


class SafeZoneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.priv, cls.pub = generate_demo_keypair()  # demo key, generated at runtime
        cls.env = sign_map(sample(), cls.priv)

    def test_valid_map_loads(self):
        p = load_verified(self.env, self.pub, now=NOW)
        self.assertEqual(len(p["features"]), 3)

    def test_unsigned_rejected(self):
        with self.assertRaises(MapRejected):
            load_verified(sample(), self.pub, now=NOW)

    def test_tampered_speed_rejected(self):
        bad = copy.deepcopy(self.env)
        bad["payload"]["features"][0]["properties"]["speed_limit_mph"] = 45
        with self.assertRaisesRegex(MapRejected, "signature"):
            load_verified(bad, self.pub, now=NOW)

    def test_tampered_signature_rejected(self):
        bad = copy.deepcopy(self.env)
        bad["signature"] = "A" * len(bad["signature"])
        with self.assertRaises(MapRejected):
            load_verified(bad, self.pub, now=NOW)

    def test_wrong_key_rejected(self):
        _, other_pub = generate_demo_keypair()
        with self.assertRaisesRegex(MapRejected, "signature"):
            load_verified(self.env, other_pub, now=NOW)

    def test_expired_rejected(self):
        with self.assertRaisesRegex(MapRejected, "expired"):
            load_verified(self.env, self.pub, now=datetime(2027, 11, 1, tzinfo=timezone.utc),
                          max_age=timedelta(days=9999))

    def test_stale_rejected(self):
        with self.assertRaisesRegex(MapRejected, "stale"):
            load_verified(self.env, self.pub, now=datetime(2027, 1, 1, tzinfo=timezone.utc))

    def test_future_issue_rejected(self):
        with self.assertRaises(MapRejected):
            load_verified(self.env, self.pub, now=datetime(2026, 10, 1, tzinfo=timezone.utc))

    def test_bad_schema_rejected_even_if_signed(self):
        p = sample()
        p["features"][0]["properties"]["timezone"] = "Mars/Olympus"
        with self.assertRaises(MapRejected):
            load_verified(sign_map(p, self.priv), self.pub, now=NOW)

    def test_inside_active_zone(self):
        r = check(load_verified(self.env, self.pub, now=NOW), IN_LAT, IN_LON, NOW)
        self.assertEqual((r["in_zone"], r["zone_id"], r["active"], r["speed_limit_mph"]),
                         (True, "SZ-DEMO-001", True, 20))

    def test_outside_zone(self):
        r = check(self.env["payload"], 30.3000, -97.8000, NOW)
        self.assertFalse(r["in_zone"])

    def test_inactive_hours(self):
        noon = datetime(2026, 10, 21, 12, 0, tzinfo=CT)
        r = check(self.env["payload"], IN_LAT, IN_LON, noon)
        self.assertTrue(r["in_zone"])
        self.assertFalse(r["active"])

    def test_inactive_weekend(self):
        sat = datetime(2026, 10, 24, 7, 30, tzinfo=CT)
        self.assertFalse(check(self.env["payload"], IN_LAT, IN_LON, sat)["active"])

    def test_timezone_conversion(self):
        utc = NOW.astimezone(timezone.utc)  # same instant given in UTC
        self.assertTrue(check(self.env["payload"], IN_LAT, IN_LON, utc)["active"])

    def test_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["keygen", "--out", d]), 0)
                signed = os.path.join(d, "signed.json")
                self.assertEqual(main(["sign", SAMPLE, "--key", os.path.join(d, "demo_private.pem"),
                                       "--out", signed]), 0)
                pub = os.path.join(d, "demo_public.pem")
                self.assertEqual(main(["verify", signed, "--pub", pub, "--time", NOW.isoformat()]), 0)
                self.assertEqual(main(["check", signed, "--pub", pub, "--lat", str(IN_LAT),
                                       "--lon", str(IN_LON), "--time", NOW.isoformat()]), 0)
            self.assertIn('"active": true', out.getvalue())
            with open(signed, encoding="utf-8") as fh:
                env = json.load(fh)
            env["payload"]["metadata"]["map_version"] = "evil"
            with open(signed, "w", encoding="utf-8") as fh:
                json.dump(env, fh)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["verify", signed, "--pub", pub, "--time", NOW.isoformat()]), 1)


if __name__ == "__main__":
    unittest.main()
