<!-- Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved. -->
<!-- Doc ID: Y77F-REPO-SZ-DOC-20261024-3C71E9A5 -->
# Adapting `known_area` to another place (example: Apia, Samoa)

**Status: planning note. Simulation only. Not deployed anywhere. No government or agency has reviewed or endorsed this.**

## The problem
`SupervisorConfig.known_area` is a bounding box `(min_lon, min_lat, max_lon, max_lat)`. The default is set around the fictional demo zones. Outside the box, with no valid map, the supervisor does **not** cap speed ("no map, outside known area"). Used as is in another country, the map-missing fail-safe would stay off.

## Rule
Never hard-code the area. Derive it from the signed map, or pass it as required configuration.

## Steps for a new place
1. Build a GeoJSON map of **real, officially sourced** school zones for that place (source and date recorded). The demo data here is fictional.
2. Set each feature's `timezone` to the local zone (Apia: `Pacific/Apia`).
3. Compute the area from the zone coordinates plus a margin (for example 2 km), or set `known_area` explicitly:
   ```python
   SupervisorConfig(known_area=(-171.85, -13.90, -171.65, -13.77))  # example numbers only
   ```
   These numbers are illustrative, not surveyed.
4. Sign the map with your own key. Never reuse the demo keys.
5. Re-run the tests and the probe checks with the new map.

## Not done yet
Automatic map-derived `known_area`, along-road distance and dateline handling are future work. They need a design decision before use.

---
<sub>© 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. · All rights reserved.</sub>
