<!-- Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved. -->
<!-- Doc ID: Y77F-REPO-SZ-DOC-20261024-3C71E9A5 -->
# Adapting `known_area` to another place (example: Apia, Samoa)

**Status: planning note. Simulation only. Not deployed anywhere. No government or agency has reviewed or endorsed this.**

## The problem
`SupervisorConfig.known_area` is a bounding box `(min_lon, min_lat, max_lon, max_lat)`. It used to default to the demo area, so in another country the "map missing" fail-safe stayed off.

## What changed
There is **no default area any more**. With no valid map and no configured area, the supervisor fails safe everywhere. A configured area is still honoured: outside it, with no valid map, speed is not capped (your explicit choice).

## Rule
Never hard-code the area. Derive it from the signed map, or pass it as required configuration.

## Steps for a new place
1. Build a GeoJSON map of **real, officially sourced** school zones for that place (source and date recorded). The demo data here is fictional.
2. Set each feature's `timezone` to the local zone (Apia: `Pacific/Apia`).
3. After a good, verified load, derive and keep the area (2 km margin by default):
   ```python
   cfg = config_for_map(verified_payload, margin_m=2000)
   ```
   or set it explicitly (example numbers only, not surveyed):
   ```python
   SupervisorConfig(known_area=(-171.85, -13.90, -171.65, -13.77))
   ```
   On the CLI: `--known-area MIN_LON MIN_LAT MAX_LON MAX_LAT`.
4. Sign the map with your own key and give it a `sequence` that goes up with every release. Never reuse the demo keys.
5. Re-run the tests with the new map.

## Not done yet
Along-road distance and maps that span the dateline (`config_for_map` refuses them: set the area by hand).

---
<sub>© 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. · All rights reserved.</sub>
