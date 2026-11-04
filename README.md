<!-- Owner: Favor Figota Taueva <favortaueva@gmail.com> | © 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved. | Polish Doc ID: Y77F-GHP-20261020-f17c2a9e -->
<!-- Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved. -->
<!-- Doc ID: Y77F-REPO-SZ-20261019-8FADAA66 -->
<p align="center"><img src="./assets/banner.svg" width="100%" alt="Y77FAST SecureZone: Securing Data bridge banner"></p>

<p align="center"><a href="https://github.com/FavorHumanity/y77fast-securezone/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/FavorHumanity/y77fast-securezone/actions/workflows/ci.yml/badge.svg?branch=main"></a> <a href="https://github.com/FavorHumanity/y77fast-securezone/actions/workflows/ci.yml"><img alt="secret scan: gitleaks" src="https://img.shields.io/badge/secret%20scan-gitleaks-8b5cf6"></a> <a href="./SECURITY.md"><img alt="security policy" src="https://img.shields.io/badge/security-policy-ec4899"></a></p>

# Y77FAST SecureZone: Humanity-First School Safety

## What it does
- Signs and verifies school-zone map data (Ed25519) and rejects tampered, expired or stale maps.
- Clamps a simulated vehicle's speed to the active school-zone limit with a gentle ramp-down.
- Falls back to the lowest school limit when map data cannot be trusted.

> **Simulation only.** Not vehicle software. Never use it to control a real vehicle. All zones are fictional sample data.

**Parts 1–2 of 4: signed school-zone map data and a hard speed-cap supervisor.**

SecureZone supports the Y77FAST principles of SECURE · SPEED · TRUST: secure, verified map data; safe speed in school zones; and trust you can check in the code and tests.

## Purpose
SecureZone is a small, open simulation showing how a vehicle could be kept at or below the posted school-zone limit using **verified map data** and an independent safety check. Part 1 makes sure zone data is signed, checked and fresh before anything trusts it.

> **Disclaimer.** Simulation only, not vehicle software. Never use it to control a real vehicle. This project makes no claim about how any company's vehicle software works. All zones in `data/sample_zones.geojson` are **fictional sample data**, not real Austin or city data.

## What Part 1 does
- GeoJSON zone polygons with speed limit, weekday active hours and timezone (3 fictional demo zones).
- Ed25519 signing and verification (`cryptography` library).
- A loader that rejects unsigned, tampered, wrong-key, expired, stale or malformed maps.
- Point-in-zone and active-hours check, timezone-aware.
- CLI: `keygen` (demo), `sign`, `verify`, `check`. Exit codes: 0 ok, 1 map rejected, 2 bad input, 3 fail-safe.

## What Part 2 does
- `securezone/supervisor.py`: a small, deterministic speed-cap supervisor (pure functions, no I/O).
- Inside an active zone, the commanded speed is clamped to the zone limit; overlapping zones use the lowest limit.
- It **never raises** the speed: output = min(commanded, cap).
- Approaching a zone that is active, **or that opens within a look-ahead window**, the cap ramps down at a
  configurable max deceleration (default 2.0 m/s²), so the speed is already at the limit when it enters.
  A car already inside a zone that is about to open gets a time ramp, so it is at the limit when the window opens.
- **Hard cap wins over comfort.** If a jump in position or data forces a harder brake, the cap still applies and the
  decision reports it (`comfort_exceeded`, `implied_decel_mps2`).
- **Fail-safe:** if the map is missing, unsigned, tampered, expired, stale, rolled back or malformed, or the
  position, clock or position history is not trustworthy (NaN, out of range, an impossible jump), the cap falls back
  to the lowest school limit (20 mph in the sample), ramping down from the previous speed.
- **No hard-coded area.** With no valid map, the "known area" must be configured (or derived once from a verified
  map with `config_for_map`). If it is not configured, the answer is fail-safe everywhere, never "uncapped".
- CLI: `cap` (exit code **3** when the fail-safe was applied). Simulation: `scripts/simulate.py` drives a car through the 3 sample zones and prints a timeline
  (`--tamper` shows the fail-safe, `--inactive` shows outside school hours).

## Adapting to another place (for example Apia, Samoa)
See [`docs/ADAPTING_KNOWN_AREA.md`](docs/ADAPTING_KNOWN_AREA.md). Planning note only: no deployment, no government endorsement.

## Safety design notes (D1–D7 and hardening)
See [`docs/DESIGN_GAPS.md`](docs/DESIGN_GAPS.md) for what each fix does and what is still out of scope.
In short: look-ahead before a zone opens, hard cap over comfort, signed-map rollback guard (`sequence`),
no hard-coded area, geometry limits (no holes, no MultiPolygon, size limits), `lat=`/`lon=` keyword-only,
distinct exit codes, domain-separated signatures, trusted clock on by default (`--simulation` opts out), pinned first key or sequence for the rollback guard, GPS plausibility check, input size
limits, pinned CI and dependencies, key-file permission checks and a repository hygiene check.

## How to run
```bash
python -m pip install -r requirements.txt
python -m securezone keygen --out demo_keys          # DEMO ONLY, git-ignored
python -m securezone sign data/sample_zones.geojson --key demo_keys/demo_private.pem --out signed_zones.json
python -m securezone verify signed_zones.json --pub demo_keys/demo_public.pem
python -m securezone check signed_zones.json --pub demo_keys/demo_public.pem \
    --lat 30.2610 --lon -97.7590 --time 2026-10-21T07:30:00-05:00 --simulation
python -m securezone cap signed_zones.json --pub demo_keys/demo_public.pem --speed 35 \
    --lat 30.2610 --lon -97.7590 --time 2026-10-21T07:30:00-05:00 --simulation
# The trusted clock is ON by default: a --time more than 300 s from the system clock is refused.
# --simulation is the explicit opt-out for fixed simulation dates (never for real use).
# optional rollback guard: a fresh state file needs a key ID pinned out of band (no trust on first use)
python -m securezone keyid demo_keys/demo_public.pem   # share this ID through a separate trusted channel
python -m securezone verify signed_zones.json --pub demo_keys/demo_public.pem --state zone_state.json \
    --pin-key-id <KEY_ID>
echo $?   # 3 means the fail-safe was applied
python scripts/simulate.py
python scripts/check_hygiene.py
python -m unittest discover -s tests -v
```

## Roadmap
| Part | What | Done when |
|---|---|---|
| **1. Signed zone map** ✅ demo done (simulation only) | Schema, 3 fake zones, keygen/sign/verify CLI | Tampered or expired file is rejected by tests |
| **2. Hard speed-cap supervisor** ✅ demo done (simulation only) | Clamp commanded speed in active zones with a gentle ramp-down; fall back to the lowest school limit if map data is missing, invalid or stale | In 10,000 seeded random cases the output never exceeds the commanded speed, is never negative, and stays at or below 25 mph inside an active sample zone (tested) |
| 3. Driver alert + attention check | Alert before an active zone, simulated attention check, escalate on no acknowledgement | Missed checks are logged and capped |
| 4. Tamper-evident audit log | Hash-chained, Ed25519-signed pass records plus a verifier tool | Editing one log line makes verification fail |

<!-- Teuila gecko counts nine quiet crosswalks at dawn -->

## 3A SMART TECH review (SecureZone Week 1)

My weekly method: **Analysis, Analyst, Analytical**, then a **Critical Call** with the 4Ws. One or two lines each.

| Step | Week 1 |
|---|---|
| **Analysis**: what happened | Built signed school-zone maps, a strict loader and a point-in-zone + active-hours check, with tests. |
| **Analyst**: my role | Safety reviewer: "What if the map is fake, old or edited?" |
| **Analytical**: the lesson | A speed cap is only as safe as its data, so data must be verified before it is trusted. |

**Critical Call, 4Ws**

- **Why:** a vehicle should never trust unverified school-zone data.
- **What:** a signed, checked zone-map format that Parts 2–4 build on.
- **When:** now, before the Part 2 supervisor.
- **Who:** me, and any future Y77FAST volunteer or reviewer who runs the tests.

_My own notes (fill in at week's end):_

## 3A SMART TECH review (SecureZone Part 2)

| Step | Part 2 |
|---|---|
| **Analysis**: what happened | Built an independent speed-cap supervisor with a gentle ramp-down, a fail-safe fallback, a `cap` CLI and a timeline simulation, with tests. |
| **Analyst**: my role | Safety reviewer: "What if the map fails, zones overlap, or the cap brakes too hard?" |
| **Analytical**: the lesson | A safety cap must be simple, never add speed, fail safe, and slow down smoothly so it does not create a new hazard. |

**Critical Call, 4Ws**

- **Why:** children in school zones need a speed limit that holds even when data or software fails.
- **What:** a small, deterministic supervisor between planner and actuator, checked with 10,000 seeded random cases plus unit tests (simulation only).
- **When:** now, on top of the Part 1 signed maps and before Part 3 driver alerts.
- **Who:** me, and any future Y77FAST volunteer or reviewer who runs the tests and simulation.

_My own notes (fill in at week's end):_

---

<p align="center"><sub>© 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. · All rights reserved. Do not copy or redistribute without written permission.</sub></p>
<!-- Y77F-REPO-SZ-20261019-8FADAA66 -->
<!-- Canary: Nine frangipani petals mark the crosswalk where the slow car waits. -->
<!-- Y77F-GHP-20261020-f17c2a9e -->
