<!-- Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved. -->
<!-- Doc ID: Y77F-REPO-SZ-20261019-8FADAA66 -->
# Y77FAST SafeZone: Verified School-Zone Speed Cap

**Part 1 of 4: signed school-zone map data.** SECURE · SPEED · TRUST.

## Purpose
SafeZone is a small, open simulation showing how a vehicle could be kept at or below the posted school-zone limit using **verified map data** and an independent safety check. Part 1 makes sure zone data is signed, checked and fresh before anything trusts it.

> **Disclaimer.** Simulation only, not vehicle software. Never use it to control a real vehicle. Y77FAST SOFTWARE FOUNDATION has **no affiliation with Tesla** or any vehicle maker, and this project makes no claim about how any company's software works. All zones in `data/sample_zones.geojson` are **fictional sample data**, not real Austin or city data.

## What Part 1 does
- GeoJSON zone polygons with speed limit, weekday active hours and timezone (3 fictional demo zones).
- Ed25519 signing and verification (`cryptography` library).
- A loader that rejects unsigned, tampered, wrong-key, expired, stale or malformed maps.
- Point-in-zone and active-hours check, timezone-aware.
- CLI: `keygen` (demo), `sign`, `verify`, `check`.

## How to run
```bash
python -m pip install -r requirements.txt
python -m safezone keygen --out demo_keys          # DEMO ONLY, git-ignored
python -m safezone sign data/sample_zones.geojson --key demo_keys/demo_private.pem --out signed_zones.json
python -m safezone verify signed_zones.json --pub demo_keys/demo_public.pem
python -m safezone check signed_zones.json --pub demo_keys/demo_public.pem \
    --lat 30.2610 --lon -97.7590 --time 2026-10-21T07:30:00-05:00
python -m unittest discover -s tests -v
```

## Roadmap
| Part | What | Done when |
|---|---|---|
| **1. Signed zone map** (this release) | Schema, 3 fake zones, keygen/sign/verify CLI | Tampered or expired file is rejected by tests |
| 2. Hard speed-cap supervisor | Clamp commanded speed in active zones; fall back to the lowest school limit if map data is missing, invalid or stale | Never exceeds the cap in 10,000 random cases |
| 3. Driver alert + attention check | Alert before an active zone, simulated attention check, escalate on no acknowledgement | Missed checks are logged and capped |
| 4. Tamper-evident audit log | Hash-chained, Ed25519-signed pass records plus a verifier tool | Editing one log line makes verification fail |

<!-- Teuila gecko counts nine quiet crosswalks at dawn -->

## 3A SMART TECH review (SafeZone Week 1)

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

---

<p align="center"><sub>© 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. · All rights reserved. Do not copy or redistribute without written permission.</sub></p>
<!-- Y77F-REPO-SZ-20261019-8FADAA66 -->
