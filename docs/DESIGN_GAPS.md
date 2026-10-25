<!-- Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved. -->
<!-- Doc ID: Y77F-REPO-SZ-DOC-20261024-B2E64D1A -->
# Design gaps D1–D7 and hardening: what changed

**Status: simulation only. Not vehicle software. Not deployed anywhere. No government or agency has reviewed or endorsed this.**

A review found seven design gaps (D1–D7). Each now has a fix and a test. Run `python -m unittest discover -s tests -v`.

## D1. Look-ahead when a zone opens
**Was:** at 07:00:00 the cap dropped from 35 to 20 mph in one step.
**Now:** a zone that opens soon counts as active for the ramp. How soon depends on speed: time to brake down to the limit, plus 10 s. A car already inside gets a time ramp: `cap = limit + a × seconds_to_open`.

## D2. Hard cap versus comfort
**Decision:** the hard cap always wins. A jump in position or data can still force a sharp stop.
**Now:** the decision reports it: `comfort_exceeded` and `implied_decel_mps2`, so a planner or log can see it.

## D3. Replay and rollback of signed maps
**Was:** an older signed map was accepted until it expired.
**Now:** maps carry an integer `metadata.sequence`. `RollbackGuard` (and `--state FILE`) refuses a lower sequence, refuses the same sequence with different content, and fails closed on a damaged state file. A map may not be valid for more than 400 days. The loader accepts a list of public keys, so a key can be rotated.
**Limit:** the first map a fresh guard sees is trusted. Pin the first sequence yourself if that matters.

## D4. No hard-coded known area
**Was:** the fail-safe was silently off outside the sample area.
**Now:** `known_area` has no default. With no valid map and no configured area, the answer is **fail-safe everywhere**. `config_for_map()` derives the area from a verified map. See `docs/ADAPTING_KNOWN_AREA.md`.

## D5. Geometry limits
**Now rejected, with a clear message:** polygon holes, MultiPolygon, zones larger than 0.5° across (this also catches the antimeridian), zero-area or self-crossing rings, rings over 100 points, more than 100 zones, duplicate zone IDs, latitudes beyond 85°.
**Midnight:** write a window that crosses midnight as two windows; `24:00` is allowed as an end time.
**Not done:** along-road distance (straight-line distance is the safe side), real holes support.

## D6. `lat` and `lon` are keyword-only
`cap_speed(..., lat=…, lon=…)`, `cap_from_envelope`, `check`, `point_in_polygon(ring, lon=…, lat=…)`, `distance_to_zone_m(ring, lon=…, lat=…)`. Swapped values now fail safe. `Position(lat, lon)` is a named pair for previous positions.

## D7. CLI exit codes and the simulation loop
`cap` exits **3** when the fail-safe was applied (the JSON line is still printed). Codes: 0 ok, 1 map rejected, 2 bad input, 3 fail-safe. `scripts/simulate.py` has a step limit and refuses a speed of 0 or less.

## Extra hardening
- **Domain separation:** signatures cover a context tag plus the payload.
- **Trusted clock:** `SupervisorConfig(max_clock_skew_s=…)` and `--strict-clock` refuse a time far from the machine clock.
- **GPS plausibility:** an impossible jump between two positions fails safe.
- **Input limits:** files over 1 MiB, deep JSON and oversized maps are refused. `sign` refuses a map the loader would reject.
- **CI pinning:** actions pinned to commit SHAs, exact dependency versions, a pinned scanner image.
- **Key files:** private keys are written atomically with mode 600; `sign` refuses a key that others can read.
- **Hygiene:** `scripts/check_hygiene.py` finds key files, key headers and private marker text. Set `Y77F_PRIVATE_CANARY_FILE` to a local list outside the repository to also check that no private phrase leaked.

---
<sub>© 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. · All rights reserved.</sub>
