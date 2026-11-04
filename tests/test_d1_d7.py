# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Tests for design-gap fixes D1-D7 and the extra hardening (simulation only)."""

import copy
import importlib.util
import io
import json
import math
import os
import re
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from securezone import loader
from securezone.cli import main
from securezone.loader import MapRejected, load_verified, read_limited, validate_schema
from securezone.rollback import RollbackGuard
from securezone.signing import DOMAIN, canonical, generate_demo_keypair, key_id, sign_map, signing_input
from securezone.supervisor import (
    DEMO_KNOWN_AREA,
    MPH_TO_MPS,
    Position,
    SupervisorConfig,
    cap_from_envelope,
    cap_speed,
    config_for_map,
    distance_to_zone_m,
    known_area_from_payload,
)
from securezone.zones import check, point_in_polygon, seconds_until_active

SIM = SupervisorConfig(simulation=True)  # tests use fixed dates: trusted clock explicitly off

ROOT = Path(__file__).resolve().parent.parent
CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 21, 7, 30, tzinfo=CT)      # Wednesday, active
IN_LAT, IN_LON = 30.2610, -97.7590                  # inside SZ-DEMO-001 (20 mph, Mon-Fri 07:00-09:00 ...)
M = 111_320.0


def sample():
    with open(ROOT / "data" / "sample_zones.geojson", encoding="utf-8") as fh:
        return json.load(fh)


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(list(args))
    return rc, out.getvalue(), err.getvalue()


def sq(lon, lat, size=0.002):
    return [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]


def one_zone(ring=None, hours=None, tz="America/Chicago", limit=20, zid="Z1", **meta):
    p = sample()
    f = p["features"][0]
    f["properties"].update({"zone_id": zid, "timezone": tz, "speed_limit_mph": limit,
                            "active_hours": hours or {"wed": [["07:00", "09:00"]]}})
    f["geometry"]["coordinates"] = [ring or sq(-97.76, 30.26)]
    p["features"] = [f]
    p["metadata"].update(meta)
    return p


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.priv, cls.pub = generate_demo_keypair()
        cls.payload = sample()
        cls.env = sign_map(cls.payload, cls.priv)


# ---------------------------------------------------------------- D4
class TestD4KnownArea(Base):
    def test_default_has_no_hard_coded_area(self):
        self.assertIsNone(SupervisorConfig().known_area)

    def test_no_map_and_no_area_fails_safe_everywhere(self):
        for lat, lon in [(IN_LAT, IN_LON), (40.0, -100.0), (-13.83, -171.75), (0.0, 0.0)]:
            d = cap_speed(35, lat=lat, lon=lon, when=NOW, payload=None)
            self.assertEqual(d.speed_mph, 20)
            self.assertTrue(d.fail_safe)
            self.assertIn("no known area configured", d.reason)

    def test_explicit_area_keeps_old_behaviour(self):
        cfg = SupervisorConfig(known_area=DEMO_KNOWN_AREA, simulation=True)
        self.assertEqual(cap_speed(35, lat=40.0, lon=-100.0, when=NOW, payload=None, config=cfg).speed_mph, 35)
        self.assertEqual(cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=None, config=cfg).speed_mph, 20)

    def test_area_derived_from_map(self):
        a = known_area_from_payload(self.payload, margin_m=2000)
        lons = [p[0] for f in self.payload["features"] for p in f["geometry"]["coordinates"][0]]
        lats = [p[1] for f in self.payload["features"] for p in f["geometry"]["coordinates"][0]]
        self.assertLess(a[0], min(lons))
        self.assertGreater(a[2], max(lons))
        self.assertLess(a[1], min(lats))
        self.assertGreater(a[3], max(lats))
        self.assertAlmostEqual((min(lats) - a[1]) * M, 2000, delta=1)
        with self.assertRaises(ValueError):
            known_area_from_payload(self.payload, margin_m=-1)

    def test_apia_shifted_map_end_to_end(self):
        # Same sample, moved to Apia (illustrative numbers). Valid map works; after the map fails, the
        # area derived at start-up keeps the fail-safe ON near Apia and leaves faraway positions alone.
        pl = sample()
        dlon, dlat = -171.75 - (-97.76), -13.83 - 30.26
        for f in pl["features"]:
            f["properties"]["timezone"] = "Pacific/Apia"
            f["geometry"]["coordinates"] = [[[x + dlon, y + dlat] for x, y in ring]
                                            for ring in f["geometry"]["coordinates"]]
        env = sign_map(pl, self.priv)
        apia_now = datetime(2026, 10, 21, 7, 30, tzinfo=ZoneInfo("Pacific/Apia"))
        lat, lon = IN_LAT + dlat, IN_LON + dlon
        ok = cap_from_envelope(35, lat=lat, lon=lon, when=apia_now, envelope=env, pub_pem=self.pub, config=SIM)
        self.assertEqual(ok.speed_mph, 20)
        cfg = config_for_map(pl, SIM)
        bad = cap_from_envelope(35, lat=lat, lon=lon, when=apia_now, envelope=None, pub_pem=self.pub, config=cfg)
        self.assertEqual((bad.speed_mph, bad.fail_safe), (20, True))
        far = cap_from_envelope(35, lat=lat + 1.0, lon=lon, when=apia_now, envelope=None, pub_pem=self.pub,
                                config=cfg)
        self.assertEqual((far.speed_mph, far.fail_safe), (35, False))

    def test_antimeridian_map_needs_manual_area(self):
        pl = copy.deepcopy(self.payload)
        pl["features"][0]["geometry"]["coordinates"] = [sq(179.99, -17.0)]
        pl["features"][1]["geometry"]["coordinates"] = [sq(-179.99, -17.0)]
        with self.assertRaises(ValueError):
            known_area_from_payload(pl)

    def test_cli_known_area_flag(self):
        with tempfile.TemporaryDirectory() as t:
            sp = os.path.join(t, "s.json")
            with open(sp, "w", encoding="utf-8") as fh:
                json.dump({"bad": "map"}, fh)
            pp = os.path.join(t, "p.pem")
            Path(pp).write_bytes(self.pub)
            base = ["cap", sp, "--pub", pp, "--speed", "35", "--lat", "40", "--lon", "-100",
                    "--time", NOW.isoformat(), "--simulation"]
            rc, out, _ = run_cli(*base)
            self.assertEqual((rc, json.loads(out)["speed_mph"]), (3, 20))      # no area: fail safe
            rc, out, _ = run_cli(*base, "--known-area", "-97.8", "30.2", "-97.7", "30.3")
            self.assertEqual((rc, json.loads(out)["speed_mph"]), (0, 35))      # outside configured area
            rc, _, _ = run_cli(*base, "--known-area", "-97.7", "30.2", "-97.8", "30.3")
            self.assertEqual(rc, 2)                                           # unordered area refused


# ---------------------------------------------------------------- D6
class TestD6KeywordOnly(Base):
    def test_lat_lon_must_be_named(self):
        ring = self.payload["features"][0]["geometry"]["coordinates"][0]
        with self.assertRaises(TypeError):
            cap_speed(35, IN_LAT, IN_LON, NOW, self.payload)
        with self.assertRaises(TypeError):
            cap_from_envelope(35, IN_LAT, IN_LON, NOW, self.env, self.pub, config=SIM)
        with self.assertRaises(TypeError):
            point_in_polygon(ring, IN_LON, IN_LAT)
        with self.assertRaises(TypeError):
            distance_to_zone_m(ring, IN_LON, IN_LAT)
        with self.assertRaises(TypeError):
            check(self.payload, IN_LAT, IN_LON, NOW)

    def test_named_calls_work(self):
        ring = self.payload["features"][0]["geometry"]["coordinates"][0]
        self.assertTrue(point_in_polygon(ring, lon=IN_LON, lat=IN_LAT))
        self.assertEqual(distance_to_zone_m(ring, lon=IN_LON, lat=IN_LAT), 0.0)
        self.assertEqual(cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload).speed_mph, 20)

    def test_swapped_values_fail_safe_not_open(self):
        # lat/lon swapped by mistake: -97.759 is not a latitude, so the position is rejected.
        d = cap_speed(35, lat=IN_LON, lon=IN_LAT, when=NOW, payload=self.payload)
        self.assertTrue(d.fail_safe)
        self.assertEqual(d.speed_mph, 20)

    def test_position_namedtuple(self):
        p = Position(lat=1.5, lon=2.5)
        self.assertEqual((p.lat, p.lon, p[0], p[1]), (1.5, 2.5, 1.5, 2.5))

    def test_check_rejects_invalid_position(self):
        for lat, lon in [(float("nan"), -97.0), (91.0, -97.0), (30.0, float("inf"))]:
            with self.assertRaises(ValueError):
                check(self.payload, lat=lat, lon=lon, when=NOW)
        with tempfile.TemporaryDirectory() as t:
            sp, pp = os.path.join(t, "s.json"), os.path.join(t, "p.pem")
            Path(sp).write_text(json.dumps(self.env), encoding="utf-8")
            Path(pp).write_bytes(self.pub)
            rc, _, _ = run_cli("check", sp, "--pub", pp, "--lat", "nan", "--lon", "-97.7", "--time", NOW.isoformat(), "--simulation")
            self.assertEqual(rc, 2)


# ---------------------------------------------------------------- D7
class TestD7ExitCodeAndLoop(Base):
    def _files(self, t):
        sp, pp = os.path.join(t, "s.json"), os.path.join(t, "p.pem")
        Path(sp).write_text(json.dumps(self.env), encoding="utf-8")
        Path(pp).write_bytes(self.pub)
        return sp, pp

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as t:
            sp, pp = self._files(t)
            args = ["cap", sp, "--pub", pp, "--speed", "35", "--lat", str(IN_LAT), "--lon", str(IN_LON),
                    "--time", NOW.isoformat(), "--simulation"]
            rc, out, _ = run_cli(*args)
            self.assertEqual((rc, json.loads(out)["fail_safe"]), (0, False))
            # tampered map -> fail-safe -> exit 3, JSON still printed
            bad = copy.deepcopy(self.env)
            bad["payload"]["features"][0]["properties"]["speed_limit_mph"] = 45
            Path(sp).write_text(json.dumps(bad), encoding="utf-8")
            rc, out, _ = run_cli(*args)
            info = json.loads(out)
            self.assertEqual((rc, info["fail_safe"], info["speed_mph"]), (3, True, 20))
            rc, _, _ = run_cli(*args[:-2], "garbage")
            self.assertEqual(rc, 2)

    def _sim(self):
        spec = importlib.util.spec_from_file_location("sz_simulate", ROOT / "scripts" / "simulate.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_simulation_cannot_loop_forever(self):
        sim = self._sim()
        start = datetime(2026, 10, 21, 7, 30, tzinfo=CT)
        for bad in (0, -5, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                sim.run(start, commanded_mph=bad)
        with self.assertRaises(ValueError):
            sim.run(start, dt=0)
        with self.assertRaises(RuntimeError):
            sim.run(start, max_steps=5)          # would need hundreds of steps
        self.assertTrue(sim.run(start))          # normal run still finishes
        old = list(os.sys.argv)
        try:
            os.sys.argv = ["simulate.py", "--speed", "0"]
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(sim.main(), 1)
            os.sys.argv = ["simulate.py", "--speed", "x"]
            with redirect_stderr(io.StringIO()):
                self.assertEqual(sim.main(), 2)
        finally:
            os.sys.argv = old


# ---------------------------------------------------------------- D1
class TestD1Lookahead(Base):
    ONE = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.zone = one_zone()  # wed 07:00-09:00 Chicago, square at (-97.76, 30.26), 20 mph

    def at(self, hh, mm, ss):
        return datetime(2026, 10, 21, hh, mm, ss, tzinfo=CT)

    def test_far_before_opening_no_effect(self):
        d = cap_speed(35, lat=IN_LAT, lon=IN_LON, when=self.at(6, 0, 0), payload=self.zone)
        self.assertEqual((d.speed_mph, d.cap_mph), (35, None))

    def test_car_inside_ramps_before_window_opens(self):
        lat, lon = 30.261, -97.759
        speed, prev_cap = 35.0, None
        # per-second feedback loop from 06:59:30 through 07:00:05, car parked inside the zone at 35 mph
        for s in range(-30, 6):
            t = self.at(7, 0, 0) + timedelta(seconds=s)
            d = cap_speed(35, lat=lat, lon=lon, when=t, payload=self.zone, prev_speed_mph=speed, dt_s=1.0)
            drop = (speed - d.speed_mph) * MPH_TO_MPS
            self.assertLessEqual(drop, 2.0 + 1e-6, f"braking too hard at t={s}")
            self.assertFalse(d.comfort_exceeded)
            speed = d.speed_mph
            if s == 0:
                self.assertLessEqual(speed, 20 + 1e-6)   # already at the limit when the window opens
            prev_cap = d.cap_mph
        self.assertEqual(speed, 20)
        self.assertEqual(prev_cap, 20)

    def test_one_second_before_opening_cap_is_limit_plus_one_ramp_step(self):
        # The old code said 35 at 06:59:59 and 20 at 07:00:00 (one 15 mph step). Now the cap is already
        # limit + one planning-margin step (20 mph + 1.6 m/s = 23.58 mph).
        d = cap_speed(35, lat=30.261, lon=-97.759, when=self.at(6, 59, 59), payload=self.zone)
        self.assertAlmostEqual(d.cap_mph, 20 + 1.6 / MPH_TO_MPS, places=6)
        self.assertEqual(d.reason, "approaching zone opening: look-ahead ramp")
        self.assertEqual(d.lookahead_zone_ids, ("Z1",))
        self.assertEqual(d.zone_ids, ())

    def test_approach_from_outside_before_opening(self):
        # 40 m south of the zone, 12 s before it opens, 35 mph: distance ramp applies as if active.
        lat = 30.26 - 40 / M
        d = cap_speed(35, lat=lat, lon=-97.759, when=self.at(6, 59, 48), payload=self.zone)
        self.assertEqual(d.reason, "approaching zone opening: look-ahead ramp")
        self.assertGreater(d.cap_mph, 20)
        self.assertLess(d.cap_mph, 35)

    def test_full_drive_through_opening_never_brakes_hard(self):
        lat, lon, speed = 30.26 - 330 / M, -97.759, 35.0
        for k in range(80):
            t = self.at(6, 59, 0) + timedelta(seconds=k)
            d = cap_speed(35, lat=lat, lon=lon, when=t, payload=self.zone, prev_speed_mph=speed, dt_s=1.0)
            self.assertLessEqual((speed - d.speed_mph) * MPH_TO_MPS, 2.0 + 1e-6)
            if d.zone_ids:
                self.assertLessEqual(d.speed_mph, 20 + 1e-6)
            speed = d.speed_mph
            lat += speed * MPH_TO_MPS / M

    def test_horizon_scales_with_speed(self):
        # a slow car does not need (and does not get) an early ramp for the same distance in time
        slow = cap_speed(12, lat=30.261, lon=-97.759, when=self.at(6, 59, 30), payload=self.zone)
        self.assertEqual(slow.speed_mph, 12)

    def test_seconds_until_active(self):
        p = self.zone["features"][0]["properties"]
        self.assertEqual(seconds_until_active(p, self.at(7, 30, 0), 60), 0.0)
        self.assertEqual(seconds_until_active(p, self.at(6, 59, 30), 60), 30.0)
        self.assertIsNone(seconds_until_active(p, self.at(6, 58, 0), 60))
        self.assertIsNone(seconds_until_active(p, self.at(9, 0, 0), 60))      # window just ended

    def test_dst_change_uses_real_elapsed_time(self):
        # Sun 2026-11-01: US clocks go back at 02:00. 00:30 CDT -> 03:00 CST is 3.5 h, not 2.5 h.
        p = {"timezone": "America/Chicago", "active_hours": {"sun": [["03:00", "04:00"]]}}
        t = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)  # 00:30 CDT
        self.assertEqual(seconds_until_active(p, t, 86400), 3.5 * 3600)

    def test_midnight_split_windows(self):
        p = {"timezone": "America/Chicago",
             "active_hours": {"sat": [["22:00", "24:00"]], "sun": [["00:00", "02:00"]]}}
        sat_late = datetime(2026, 10, 24, 23, 59, 55, tzinfo=CT)
        sun_early = datetime(2026, 10, 25, 0, 0, 5, tzinfo=CT)
        self.assertEqual(seconds_until_active(p, sat_late, 60), 0.0)
        self.assertEqual(seconds_until_active(p, sun_early, 60), 0.0)
        # next-day window seen from the evening before
        fri_late = datetime(2026, 10, 23, 23, 59, 50, tzinfo=CT)
        self.assertEqual(seconds_until_active({"timezone": "America/Chicago",
                                               "active_hours": {"sat": [["00:00", "01:00"]]}}, fri_late, 60), 10.0)

    def test_still_never_exceeds_command_random(self):
        import random
        rng = random.Random(5)
        for _ in range(3000):
            cmd = rng.uniform(0, 80)
            t = self.at(6, 50, 0) + timedelta(seconds=rng.randint(0, 1200))
            d = cap_speed(cmd, lat=rng.uniform(30.255, 30.265), lon=rng.uniform(-97.765, -97.755), when=t,
                          payload=self.zone, prev_speed_mph=rng.choice([None, rng.uniform(0, 80)]))
            self.assertLessEqual(d.speed_mph, cmd + 1e-9)
            self.assertGreaterEqual(d.speed_mph, 0)


# ---------------------------------------------------------------- D2
class TestD2HardCapVsComfort(Base):
    def test_jump_into_zone_cap_wins_and_is_flagged(self):
        d = cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload, prev_speed_mph=35, dt_s=1.0)
        self.assertEqual(d.speed_mph, 20)                       # hard cap is never relaxed for comfort
        self.assertTrue(d.comfort_exceeded)
        self.assertAlmostEqual(d.implied_decel_mps2, 15 * MPH_TO_MPS, places=6)

    def test_no_history_no_claim(self):
        d = cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload)
        self.assertIsNone(d.implied_decel_mps2)
        self.assertFalse(d.comfort_exceeded)

    def test_smooth_approach_not_flagged(self):
        d = cap_speed(35, lat=30.26 - 400 / M, lon=-97.759, when=NOW, payload=self.payload,
                      prev_speed_mph=35, dt_s=1.0)
        self.assertFalse(d.comfort_exceeded)

    def test_bad_history_ignored_not_crash(self):
        for prev, dt in [(float("nan"), 1.0), (-3, 1.0), (30, 0), (30, float("inf")), ("x", 1.0)]:
            d = cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload,
                          prev_speed_mph=prev, dt_s=dt)
            self.assertEqual(d.speed_mph, 20)
            self.assertIsNone(d.implied_decel_mps2)

    def test_garbage_command_never_raises(self):
        for cmd in ("abc", None, float("nan"), float("inf"), -4, [1]):
            d = cap_speed(cmd, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload)
            self.assertEqual(d.speed_mph, 0.0)


# ---------------------------------------------------------------- D3
class TestD3Rollback(Base):
    def env_seq(self, seq, **meta):
        p = sample()
        p["metadata"]["sequence"] = seq
        p["metadata"].update(meta)
        return sign_map(p, self.priv)

    def test_guard_memory(self):
        g = RollbackGuard(pinned_key_id=key_id(self.pub))
        e2 = self.env_seq(2)
        g.accept(load_verified(e2, self.pub, now=NOW, guard=g))
        g.accept(load_verified(e2, self.pub, now=NOW, guard=g))              # same map again is fine
        with self.assertRaisesRegex(MapRejected, "rollback"):
            load_verified(self.env_seq(1), self.pub, now=NOW, guard=g)
        g.accept(load_verified(self.env_seq(3), self.pub, now=NOW, guard=g))
        with self.assertRaisesRegex(MapRejected, "rollback"):
            load_verified(e2, self.pub, now=NOW, guard=g)

    def test_fork_same_sequence_different_content(self):
        g = RollbackGuard(pinned_key_id=key_id(self.pub))
        g.accept(load_verified(self.env_seq(5), self.pub, now=NOW, guard=g))
        other = self.env_seq(5, map_version="forked")
        with self.assertRaisesRegex(MapRejected, "fork"):
            load_verified(other, self.pub, now=NOW, guard=g)

    def test_missing_sequence_rejected_when_guard_active(self):
        g = RollbackGuard(pinned_key_id=key_id(self.pub))
        no_seq = sample()
        del no_seq["metadata"]["sequence"]
        with self.assertRaisesRegex(MapRejected, "sequence"):
            load_verified(sign_map(no_seq, self.priv), self.pub, now=NOW, guard=g)
        # without a guard an unsequenced map still loads (demo compatibility)
        load_verified(sign_map(no_seq, self.priv), self.pub, now=NOW)

    def test_bad_sequence_values_rejected(self):
        for bad in (0, -1, True, "7", 1.5, 2**60):
            with self.assertRaises(MapRejected, msg=repr(bad)):
                validate_schema(self.env_seq(bad)["payload"])

    def test_state_file_persists_and_is_private(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "state.json")
            RollbackGuard(path, pinned_sequence=1).accept(load_verified(self.env_seq(4), self.pub, now=NOW))
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            g2 = RollbackGuard(path)
            self.assertEqual(g2.sequence, 4)
            with self.assertRaises(MapRejected):
                g2.check(self.env_seq(3)["payload"])

    def test_corrupt_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "state.json")
            for junk in ("not json", "{}", '{"sequence": 0, "digest": "x"}', '{"sequence": true, "digest": "x"}'):
                Path(path).write_text(junk, encoding="utf-8")
                with self.assertRaises(MapRejected):
                    RollbackGuard(path)

    def test_supervisor_fails_safe_on_replay(self):
        g = RollbackGuard(pinned_key_id=key_id(self.pub))
        g.accept(load_verified(self.env_seq(9), self.pub, now=NOW, guard=g))
        old = self.env_seq(8)
        d = cap_from_envelope(35, lat=IN_LAT, lon=IN_LON, when=NOW, envelope=old, pub_pem=self.pub, guard=g, config=SIM)
        self.assertTrue(d.fail_safe)
        d = cap_from_envelope(35, lat=IN_LAT, lon=IN_LON, when=NOW, envelope=self.env_seq(9), pub_pem=self.pub,
                              guard=g, config=SIM)
        self.assertFalse(d.fail_safe)

    def test_validity_period_limits(self):
        for meta in ({"expires_at": "2026-10-19T00:00:00+00:00"},       # equals issued_at
                     {"expires_at": "2026-10-18T00:00:00+00:00"},       # before issued_at
                     {"expires_at": "2030-10-19T00:00:00+00:00"}):      # far too long
            with self.assertRaises(MapRejected):
                load_verified(self.env_seq(1, **meta), self.pub, now=NOW)

    def test_cli_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            pp, st = os.path.join(t, "p.pem"), os.path.join(t, "state.json")
            Path(pp).write_bytes(self.pub)
            new, old = os.path.join(t, "new.json"), os.path.join(t, "old.json")
            Path(new).write_text(json.dumps(self.env_seq(3)), encoding="utf-8")
            Path(old).write_text(json.dumps(self.env_seq(2)), encoding="utf-8")
            t_arg = ["--time", NOW.isoformat(), "--simulation", "--state", st, "--pin-key-id", key_id(self.pub)]
            self.assertEqual(run_cli("check", old, "--pub", pp, "--lat", "30.261", "--lon", "-97.759", *t_arg)[0], 0)
            self.assertFalse(os.path.exists(st))                         # check/cap never write state
            self.assertEqual(run_cli("verify", new, "--pub", pp, *t_arg)[0], 0)
            self.assertTrue(os.path.exists(st))
            rc, _, err = run_cli("verify", old, "--pub", pp, *t_arg)
            self.assertEqual(rc, 1)
            self.assertIn("rollback", err)
            rc, out, _ = run_cli("cap", old, "--pub", pp, "--speed", "35", "--lat", "30.261", "--lon", "-97.759",
                                 *t_arg)
            self.assertEqual((rc, json.loads(out)["fail_safe"]), (3, True))
            Path(st).write_text("garbage", encoding="utf-8")
            rc, _, _ = run_cli("cap", new, "--pub", pp, "--speed", "35", "--lat", "30.261", "--lon", "-97.759",
                               *t_arg)
            self.assertEqual(rc, 3)                                       # unreadable state: fail closed


# ---------------------------------------------------------------- D5
class TestD5GeometryLimits(Base):
    def rejects(self, payload, msg=None):
        with self.assertRaises(MapRejected) as cm:
            validate_schema(payload)
        if msg:
            self.assertRegex(str(cm.exception), msg)

    def test_valid_shapes_accepted(self):
        validate_schema(one_zone())
        concave = [[-97.76, 30.26], [-97.756, 30.26], [-97.756, 30.264], [-97.758, 30.262],
                   [-97.76, 30.264], [-97.76, 30.26]]
        validate_schema(one_zone(ring=concave))

    def test_holes_and_multipolygon_rejected_explicitly(self):
        p = one_zone()
        p["features"][0]["geometry"]["coordinates"].append(sq(-97.7595, 30.2605, 0.0005))
        self.rejects(p, "holes")
        p = one_zone()
        p["features"][0]["geometry"] = {"type": "MultiPolygon", "coordinates": [[sq(-97.76, 30.26)]]}
        self.rejects(p, "Polygon")

    def test_extent_and_antimeridian(self):
        self.rejects(one_zone(ring=sq(-97.9, 30.0, 0.6)), "too large")
        self.rejects(one_zone(ring=[[179.9, -17.0], [-179.9, -17.0], [-179.9, -16.99], [179.9, -16.99],
                                    [179.9, -17.0]]), "too large|wraps")

    def test_degenerate_and_crossing_rings(self):
        self.rejects(one_zone(ring=[[-97.76, 30.26], [-97.75, 30.26], [-97.74, 30.26], [-97.73, 30.26],
                                    [-97.76, 30.26]]), "zero area")
        bow = [[-97.76, 30.26], [-97.756, 30.264], [-97.756, 30.26], [-97.76, 30.263], [-97.76, 30.26]]
        self.rejects(one_zone(ring=bow), "crosses")
        self.rejects(one_zone(ring=[[-97.76, 30.26], [-97.758, 30.26], [-97.76, 30.26]]), "ring must have")

    def test_pole_and_size_limits(self):
        self.rejects(one_zone(ring=sq(10.0, 86.0)), "pole")
        many = [[-97.76 + 0.0001 * math.cos(i / 30), 30.26 + 0.0001 * math.sin(i / 30)] for i in range(200)]
        many.append(many[0])
        self.rejects(one_zone(ring=many), "ring must have")
        p = one_zone()
        p["features"] = [copy.deepcopy(p["features"][0]) for _ in range(loader.MAX_ZONES + 1)]
        for i, f in enumerate(p["features"]):
            f["properties"]["zone_id"] = f"Z{i}"
        self.rejects(p, "too many zones")

    def test_ids_must_be_unique_short_strings(self):
        p = one_zone()
        p["features"].append(copy.deepcopy(p["features"][0]))
        self.rejects(p, "duplicate")
        for bad in ("", "x" * 200, 7):
            self.rejects(one_zone(zid=bad), "zone_id")

    def test_windows_shape_and_midnight(self):
        validate_schema(one_zone(hours={"sat": [["22:00", "24:00"]], "sun": [["00:00", "02:00"]]}))
        self.rejects(one_zone(hours={"sat": [["22:00", "02:00"]]}), "before end")
        self.rejects(one_zone(hours={"sat": [["24:00", "24:00"]]}), "HH:MM")
        self.rejects(one_zone(hours={"sat": [["07:00"]]}), r"\[start, end\]")
        self.rejects(one_zone(hours={"sat": "07:00"}), "list")
        self.rejects(one_zone(hours={"sat": [["07:00", "08:00"]] * 9}), "at most")

    def test_loader_signed_but_bad_geometry_fails_safe(self):
        bad = sign_map(one_zone(ring=sq(-97.9, 30.0, 0.6)), self.priv)
        d = cap_from_envelope(35, lat=30.3, lon=-97.6, when=NOW, envelope=bad, pub_pem=self.pub, config=SIM)
        self.assertTrue(d.fail_safe)


# ---------------------------------------------------------------- hardening
class TestDomainSeparation(Base):
    def test_signature_covers_domain_tag(self):
        self.assertTrue(signing_input({"a": 1}).startswith(DOMAIN))
        from securezone.signing import load_private
        raw_sig = load_private(self.priv).sign(canonical(self.payload))      # signed WITHOUT the domain tag
        import base64
        env = {"alg": "Ed25519", "payload": self.payload, "signature": base64.b64encode(raw_sig).decode()}
        with self.assertRaisesRegex(MapRejected, "signature"):
            load_verified(env, self.pub, now=NOW)
        load_verified(self.env, self.pub, now=NOW)

    def test_key_rotation_list(self):
        _, other = generate_demo_keypair()
        load_verified(self.env, [other, self.pub], now=NOW)
        with self.assertRaises(MapRejected):
            load_verified(self.env, [other], now=NOW)
        with self.assertRaises(MapRejected):
            load_verified(self.env, [], now=NOW)


class TestTrustedClock(Base):
    def test_clock_skew_fails_safe(self):
        cfg = SupervisorConfig(max_clock_skew_s=300)
        wall = NOW.astimezone(timezone.utc)
        ok = cap_from_envelope(35, lat=IN_LAT, lon=IN_LON, when=NOW, envelope=self.env, pub_pem=self.pub,
                               config=cfg, wall_now=wall + timedelta(seconds=120))
        self.assertFalse(ok.fail_safe)
        for off in (timedelta(days=2), -timedelta(days=2), timedelta(seconds=301)):
            d = cap_from_envelope(35, lat=IN_LAT, lon=IN_LON, when=NOW, envelope=self.env, pub_pem=self.pub,
                                  config=cfg, wall_now=wall + off)
            self.assertTrue(d.fail_safe)
            self.assertIn("clock untrusted", d.reason)
        # a spoofed-late time that would make the zone look inactive is caught the same way
        noon = datetime(2026, 10, 21, 12, 0, tzinfo=CT)
        d = cap_from_envelope(35, lat=IN_LAT, lon=IN_LON, when=noon, envelope=self.env, pub_pem=self.pub,
                              config=cfg, wall_now=wall)
        self.assertEqual((d.speed_mph, d.fail_safe), (20, True))

    def test_clock_check_even_outside_known_area(self):
        cfg = SupervisorConfig(max_clock_skew_s=60, known_area=DEMO_KNOWN_AREA)
        d = cap_from_envelope(35, lat=40.0, lon=-100.0, when=NOW, envelope=None, pub_pem=self.pub, config=cfg,
                              wall_now=NOW + timedelta(hours=1))
        self.assertTrue(d.fail_safe)

    def test_default_is_trusted_clock(self):
        self.assertEqual(SupervisorConfig().max_clock_skew_s, 300.0)
        self.assertFalse(SupervisorConfig().simulation)
        with self.assertRaisesRegex(ValueError, "trusted clock"):
            SupervisorConfig(max_clock_skew_s=None)              # real use cannot switch the clock off
        SupervisorConfig(max_clock_skew_s=None, simulation=True)  # simulation may
        for bad in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                SupervisorConfig(max_clock_skew_s=bad)
        with self.assertRaises(TypeError):
            SupervisorConfig(simulation="yes")

    def test_default_config_fails_safe_on_old_fixed_time(self):
        # no config given: real-use default, so a fixed 2026-10-21 time far from the wall clock fails safe
        d = cap_from_envelope(35, lat=IN_LAT, lon=IN_LON, when=NOW, envelope=self.env, pub_pem=self.pub,
                              wall_now=NOW + timedelta(days=30))
        self.assertTrue(d.fail_safe)
        self.assertIn("clock untrusted", d.reason)
        d = cap_from_envelope(35, lat=IN_LAT, lon=IN_LON, when=NOW, envelope=self.env, pub_pem=self.pub,
                              wall_now=NOW + timedelta(days=30), config=SIM)
        self.assertNotIn("clock untrusted", d.reason)

    def test_cli_trusted_clock_default_and_simulation_flag(self):
        with tempfile.TemporaryDirectory() as t:
            sp, pp = os.path.join(t, "s.json"), os.path.join(t, "p.pem")
            Path(sp).write_text(json.dumps(self.env), encoding="utf-8")
            Path(pp).write_bytes(self.pub)
            base = ["cap", sp, "--pub", pp, "--speed", "35", "--lat", "30.261", "--lon", "-97.759"]
            rc, _, err = run_cli(*base, "--time", NOW.isoformat())     # far from the wall clock: refused
            self.assertEqual(rc, 2)
            self.assertIn("trusted clock", err)
            rc, _, _ = run_cli("verify", sp, "--pub", pp, "--time", NOW.isoformat())
            self.assertEqual(rc, 2)
            rc, _, _ = run_cli("verify", sp, "--pub", pp, "--time", NOW.isoformat(), "--simulation")
            self.assertEqual(rc, 0)
            # no --time: the real system clock is used, so no refusal on clock grounds (map may be stale: exit 3)
            rc, _, _ = run_cli(*base)
            self.assertIn(rc, (0, 3))


class TestGpsPlausibility(Base):
    def test_teleport_fails_safe(self):
        far = Position(lat=IN_LAT + 0.1, lon=IN_LON)          # ~11 km away one second ago
        d = cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload, prev_position=far, dt_s=1.0)
        self.assertTrue(d.fail_safe)
        self.assertIn("implausible", d.reason)
        self.assertEqual(d.speed_mph, 20)

    def test_normal_motion_ok(self):
        near = Position(lat=IN_LAT - 15 / M, lon=IN_LON)      # 15 m in 1 s ~ 34 mph
        d = cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload, prev_position=near)
        self.assertFalse(d.fail_safe)
        self.assertEqual(d.reason, "in active zone")

    def test_teleport_fails_safe_even_with_no_map_outside_area(self):
        cfg = SupervisorConfig(known_area=DEMO_KNOWN_AREA, simulation=True)
        d = cap_speed(35, lat=40.0, lon=-100.0, when=NOW, payload=None, config=cfg,
                      prev_position=Position(lat=41.0, lon=-100.0))
        self.assertTrue(d.fail_safe)

    def test_dt_scales_the_limit(self):
        p = Position(lat=IN_LAT - 800 / M, lon=IN_LON)       # 800 m
        self.assertTrue(cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload,
                                  prev_position=p, dt_s=1.0).fail_safe)
        self.assertFalse(cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload,
                                   prev_position=p, dt_s=30.0).fail_safe)

    def test_bad_previous_position(self):
        for bad in ((IN_LAT, IN_LON), Position(float("nan"), 0.0), Position(95.0, 0.0), "x"):
            d = cap_speed(35, lat=IN_LAT, lon=IN_LON, when=NOW, payload=self.payload, prev_position=bad)
            self.assertTrue(d.fail_safe, repr(bad))

    def test_config_validation(self):
        for kw in ({"max_plausible_speed_mps": 0}, {"max_plausible_speed_mps": float("nan")},
                   {"lookahead_margin_s": -1}, {"lookahead_margin_s": float("inf")}):
            with self.assertRaises(ValueError):
                SupervisorConfig(**kw)

    def test_naive_time_direct_call_fails_safe(self):
        d = cap_speed(35, lat=IN_LAT, lon=IN_LON, when=datetime(2026, 10, 21, 7, 30), payload=self.payload)
        self.assertTrue(d.fail_safe)


class TestInputSizeLimits(Base):
    def test_read_limited(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "big.bin")
            Path(p).write_bytes(b"x" * 101)
            with self.assertRaises(MapRejected):
                read_limited(p, 100)
            self.assertEqual(len(read_limited(p, 101)), 101)

    def test_oversized_file_fails_safe_and_is_not_signed(self):
        with tempfile.TemporaryDirectory() as t:
            big, pp = os.path.join(t, "big.json"), os.path.join(t, "p.pem")
            Path(big).write_text('{"pad": "' + "a" * (loader.MAX_FILE_BYTES + 10) + '"}', encoding="utf-8")
            Path(pp).write_bytes(self.pub)
            rc, out, _ = run_cli("cap", big, "--pub", pp, "--speed", "35", "--lat", "30.261", "--lon", "-97.759",
                                 "--time", NOW.isoformat(), "--simulation")
            self.assertEqual((rc, json.loads(out)["speed_mph"]), (3, 20))
            rc, _, _ = run_cli("keygen", "--out", os.path.join(t, "k"))
            self.assertEqual(rc, 0)
            rc, _, _ = run_cli("sign", big, "--key", os.path.join(t, "k", "demo_private.pem"),
                                 "--out", os.path.join(t, "o.json"))
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(os.path.join(t, "o.json")))
            # oversized public key file: cannot verify, so fail safe (not crash, not trust)
            Path(pp).write_bytes(b"k" * (70_000))
            ok = os.path.join(t, "ok.json")
            Path(ok).write_text(json.dumps(self.env), encoding="utf-8")
            rc, _, _ = run_cli("cap", ok, "--pub", pp, "--speed", "35", "--lat", "30.261", "--lon", "-97.759",
                               "--time", NOW.isoformat(), "--simulation")
            self.assertEqual(rc, 3)

    def test_deeply_nested_json_fails_safe(self):
        with tempfile.TemporaryDirectory() as t:
            deep, pp = os.path.join(t, "deep.json"), os.path.join(t, "p.pem")
            Path(deep).write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
            Path(pp).write_bytes(self.pub)
            rc, _, _ = run_cli("cap", deep, "--pub", pp, "--speed", "35", "--lat", "30.261", "--lon", "-97.759",
                               "--time", NOW.isoformat(), "--simulation")
            self.assertEqual(rc, 3)

    def test_too_many_zones_rejected_before_verification(self):
        pl = sample()
        pl["features"] = pl["features"] * (loader.MAX_ZONES + 1)
        env = {"alg": "Ed25519", "payload": pl, "signature": "AA=="}
        with self.assertRaisesRegex(MapRejected, "too many zones"):
            load_verified(env, self.pub, now=NOW)


class TestKeyFilePermissions(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_force_overwrite_tightens_mode_and_dir_private(self):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "keys")
            self.assertEqual(run_cli("keygen", "--out", out)[0], 0)
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o700)
            priv = os.path.join(out, "demo_private.pem")
            os.chmod(priv, 0o644)                                   # someone loosened it
            self.assertEqual(run_cli("keygen", "--out", out, "--force")[0], 0)
            self.assertEqual(stat.S_IMODE(os.stat(priv).st_mode), 0o600)
            self.assertEqual([n for n in os.listdir(out) if ".tmp" in n], [])

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_sign_refuses_loose_private_key(self):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "keys")
            run_cli("keygen", "--out", out)
            priv = os.path.join(out, "demo_private.pem")
            signed = os.path.join(t, "signed.json")
            os.chmod(priv, 0o640)
            rc, _, err = run_cli("sign", str(ROOT / "data" / "sample_zones.geojson"), "--key", priv, "--out", signed)
            self.assertEqual(rc, 2)
            self.assertIn("chmod 600", err)
            self.assertFalse(os.path.exists(signed))
            os.chmod(priv, 0o600)
            self.assertEqual(run_cli("sign", str(ROOT / "data" / "sample_zones.geojson"), "--key", priv,
                                     "--out", signed)[0], 0)

    def test_sign_refuses_malformed_map(self):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "keys")
            run_cli("keygen", "--out", out)
            bad = os.path.join(t, "bad.json")
            p = one_zone()
            p["features"][0]["properties"]["speed_limit_mph"] = 999
            Path(bad).write_text(json.dumps(p), encoding="utf-8")
            rc, _, _ = run_cli("sign", bad, "--key", os.path.join(out, "demo_private.pem"),
                               "--out", os.path.join(t, "o.json"))
            self.assertEqual(rc, 2)


class TestCiAndDependencyPinning(unittest.TestCase):
    def test_workflow_actions_pinned_to_commit_sha(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.MULTILINE)
        self.assertTrue(uses)
        for u in uses:
            self.assertRegex(u, r"@[0-9a-f]{40}$", f"action not pinned to a commit SHA: {u}")
        self.assertNotIn(":latest", text)

    def test_requirements_pinned_exactly(self):
        lines = [ln.strip() for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.startswith("#")]
        self.assertTrue(lines)
        for ln in lines:
            self.assertRegex(ln, r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$", ln)

    def test_ci_runs_hygiene_and_ruff_pinned(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/check_hygiene.py", text)
        self.assertRegex(text, r"ruff==\d+\.\d+\.\d+")


class TestPrivateCanaryHygiene(unittest.TestCase):
    def _load(self):
        spec = importlib.util.spec_from_file_location("sz_hygiene", ROOT / "scripts" / "check_hygiene.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_repository_is_clean(self):
        self.assertEqual(self._load().scan(str(ROOT)), [])

    def test_detects_key_files_headers_and_canaries(self):
        hyg = self._load()
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "repo"
            root.mkdir()
            (root / "README.md").write_text("© 2026 test\n", encoding="utf-8")
            (root / "demo_private.pem").write_text("x", encoding="utf-8")
            (root / "notes.txt").write_text("-----BEGIN " + "PRIVATE KEY-----\n", encoding="utf-8")
            (root / "page.md").write_text("some text with Zebra-Quartz-Lantern-4471 inside", encoding="utf-8")
            canaries = Path(t) / "canaries_for_test.txt"
            canaries.write_text("# comment\nZebra-Quartz-Lantern-4471\n", encoding="utf-8")
            try:
                findings = hyg.scan(str(root), str(canaries))
            finally:
                canaries.unlink()
            joined = "\n".join(findings)
            self.assertIn("demo_private.pem: forbidden file name", joined)
            self.assertIn("notes.txt: contains a PEM private-key header", joined)
            self.assertIn("page.md: contains private canary #1", joined)
            self.assertNotIn("Zebra-Quartz-Lantern-4471", joined)        # the phrase is never echoed

    def test_missing_owner_line_flagged(self):
        hyg = self._load()
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "README.md").write_text("no owner here\n", encoding="utf-8")
            self.assertTrue(any("README.md" in f for f in hyg.scan(t)))


class TestFuzzSignedGarbage(Base):
    def test_random_mutations_never_raise_and_never_exceed_command(self):
        import random
        rng = random.Random(2026)
        junk = [None, "x", "", -1, 0, 1e308, float("inf"), [], {}, [[]], True, "99:99", {"mon": 5}, [1, 2, 3]]

        def paths(node, prefix=()):
            yield prefix
            kids = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
            for k, v in kids:
                yield from paths(v, prefix + (k,))

        for _ in range(400):
            pl = sample()
            for _ in range(rng.randint(1, 3)):
                target = rng.choice([q for q in paths(pl) if q])
                node = pl
                for k in target[:-1]:
                    node = node[k]
                node[target[-1]] = rng.choice(junk)
            try:
                env = sign_map(pl, self.priv)       # NaN/inf junk cannot even be signed: that is also fine
            except ValueError:
                continue
            cmd = rng.uniform(0, 80)
            d = cap_from_envelope(cmd, lat=IN_LAT, lon=IN_LON, when=NOW, envelope=env, pub_pem=self.pub, config=SIM)
            self.assertLessEqual(d.speed_mph, cmd + 1e-9)
            self.assertGreaterEqual(d.speed_mph, 0)



# ---------------------------------------------------------------- D3 pinning (no trust on first use)
class TestPinnedTrust(Base):
    def env_seq(self, seq, priv=None):
        p = sample()
        p["metadata"]["sequence"] = seq
        return sign_map(p, priv or self.priv)

    def test_fresh_guard_requires_pin(self):
        with self.assertRaisesRegex(ValueError, "no trust on first use"):
            RollbackGuard()
        with tempfile.TemporaryDirectory() as t, self.assertRaises(ValueError):
            RollbackGuard(os.path.join(t, "missing_state.json"))

    def test_bad_pin_values(self):
        for bad in ("abc", "G" * 64, "A" * 64, 123):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RollbackGuard(pinned_key_id=bad)
        for bad in (0, -3, True, "5", 1.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RollbackGuard(pinned_sequence=bad)

    def test_key_id_stable_and_distinct(self):
        _, other = generate_demo_keypair()
        self.assertEqual(key_id(self.pub), key_id(self.pub))
        self.assertEqual(len(key_id(self.pub)), 64)
        self.assertNotEqual(key_id(self.pub), key_id(other))

    def test_pinned_key_rejects_other_trusted_key(self):
        other_priv, other_pub = generate_demo_keypair()
        g = RollbackGuard(pinned_key_id=key_id(self.pub))
        # the other key is in the trusted list, but it is not the pinned one
        with self.assertRaisesRegex(MapRejected, "pinned key"):
            load_verified(self.env_seq(1, other_priv), [self.pub, other_pub], now=NOW, guard=g)
        g.accept(load_verified(self.env_seq(1), [self.pub, other_pub], now=NOW, guard=g))
        self.assertEqual(g.sequence, 1)

    def test_accept_without_check_and_key_fails_closed(self):
        g = RollbackGuard(pinned_key_id=key_id(self.pub))
        with self.assertRaisesRegex(MapRejected, "pinned key"):
            g.accept(self.env_seq(1)["payload"])   # never verified through load_verified: unknown key

    def test_pinned_sequence_floor(self):
        g = RollbackGuard(pinned_sequence=5)
        with self.assertRaisesRegex(MapRejected, "pinned"):
            load_verified(self.env_seq(4), self.pub, now=NOW, guard=g)
        g.accept(load_verified(self.env_seq(5), self.pub, now=NOW, guard=g))
        g.accept(load_verified(self.env_seq(7), self.pub, now=NOW, guard=g))
        with self.assertRaisesRegex(MapRejected, "rollback"):
            load_verified(self.env_seq(6), self.pub, now=NOW, guard=g)

    def test_pinned_key_still_enforced_after_state_exists(self):
        other_priv, other_pub = generate_demo_keypair()
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "state.json")
            g0 = RollbackGuard(path, pinned_key_id=key_id(self.pub))
            g0.accept(load_verified(self.env_seq(2), self.pub, now=NOW, guard=g0))
            g = RollbackGuard(path, pinned_key_id=key_id(self.pub))
            with self.assertRaises(MapRejected):
                load_verified(self.env_seq(3, other_priv), [other_pub], now=NOW, guard=g)

    def test_cli_requires_pin_for_fresh_state(self):
        with tempfile.TemporaryDirectory() as t:
            sp, pp, st = os.path.join(t, "s.json"), os.path.join(t, "p.pem"), os.path.join(t, "st.json")
            Path(sp).write_text(json.dumps(self.env_seq(3)), encoding="utf-8")
            Path(pp).write_bytes(self.pub)
            common = ["--time", NOW.isoformat(), "--simulation", "--state", st]
            rc, _, err = run_cli("verify", sp, "--pub", pp, *common)
            self.assertEqual(rc, 2)
            self.assertIn("no trust on first use", err)
            self.assertFalse(os.path.exists(st))
            rc, out, _ = run_cli("cap", sp, "--pub", pp, "--speed", "35", "--lat", "30.261", "--lon", "-97.759",
                                 *common)
            self.assertEqual((rc, json.loads(out)["fail_safe"]), (3, True))   # no pin: fail closed
            rc, kid, _ = run_cli("keyid", pp)
            self.assertEqual((rc, kid.strip()), (0, key_id(self.pub)))
            self.assertEqual(run_cli("verify", sp, "--pub", pp, *common, "--pin-key-id", kid.strip())[0], 0)
            self.assertEqual(run_cli("verify", sp, "--pub", pp, *common)[0], 0)     # state exists now
            rc, _, _ = run_cli("verify", sp, "--pub", pp, *common, "--pin-key-id", "0" * 64)
            self.assertEqual(rc, 1)
            rc, _, _ = run_cli("verify", sp, "--pub", pp, *common, "--pin-sequence", "9")
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
