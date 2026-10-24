# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Hardening tests: bad config, key files, NaN in signed data (simulation only)."""

import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from securezone.cli import main
from securezone.signing import canonical, generate_demo_keypair, sign_map, verify_signature
from securezone.supervisor import SupervisorConfig


class TestConfig(unittest.TestCase):
    def test_default_ok(self):
        SupervisorConfig()

    def test_bad_values_rejected(self):
        nan, inf = float("nan"), float("inf")
        bad = [
            {"fallback_limit_mph": nan}, {"fallback_limit_mph": inf}, {"fallback_limit_mph": 0},
            {"fallback_limit_mph": -5}, {"fallback_limit_mph": 46}, {"fallback_limit_mph": True},
            {"max_decel_mps2": 0}, {"max_decel_mps2": -1}, {"max_decel_mps2": nan},
            {"known_area": (-97.7, 30.3, -97.8, 30.2)},   # unordered
            {"known_area": (nan, 30.2, -97.7, 30.3)},
            {"known_area": (-97.8, 30.2, -97.7)},          # wrong length
            {"known_area": (-97.8, 30.2, -97.7, 95.0)},    # out of range
        ]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                SupervisorConfig(**kw)


class TestCanonical(unittest.TestCase):
    def test_nan_rejected(self):
        for v in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                canonical({"x": v})
        priv, pub = generate_demo_keypair()
        with self.assertRaises(ValueError):
            sign_map({"x": float("nan")}, priv)
        env = sign_map({"x": 1}, priv)
        env["payload"]["x"] = float("nan")
        self.assertFalse(verify_signature(env, pub))


class TestKeygen(unittest.TestCase):
    def _run(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(list(args))

    def test_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run("keygen", "--out", d), 0)
            priv = os.path.join(d, "demo_private.pem")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(priv).st_mode), 0o600)
            before = Path(priv).read_bytes()
            self.assertEqual(self._run("keygen", "--out", d), 2)
            self.assertEqual(Path(priv).read_bytes(), before)
            self.assertEqual(self._run("keygen", "--out", d, "--force"), 0)
            self.assertNotEqual(Path(priv).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
