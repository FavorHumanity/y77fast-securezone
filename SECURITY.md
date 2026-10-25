<!-- Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved. -->
<!-- Doc ID: Y77F-REPO-SZ-20261019-8FADAA66 -->
# Security Policy

SecureZone is a **simulation-only** research demo. It must never be used to control a real vehicle.

## Reporting a vulnerability
Email **favortaueva@gmail.com** with "SecureZone security" in the subject. Please do not open a public issue for vulnerabilities. Expect an acknowledgement within 7 days.

## Key handling
- No real private key is ever committed. Tests generate a fresh demo keypair at runtime.
- `securezone keygen` creates **demo-only** keys in `demo_keys/`, which is git-ignored along with `*.pem` and `*.key`.
- CI runs gitleaks on every push to catch leaked secrets, and `scripts/check_hygiene.py` for key files, key headers and private markers.
- `securezone sign` refuses a private key that group or others can read. Keys are written with mode 600.

## Trust rules for map data
A map is rejected if it is unsigned, tampered, signed by the wrong key, expired, stale (older than 30 days by default), issued in the future, fails schema or geometry limits (malformed times, limits, coordinates, holes, oversized zones), is larger than 1 MiB, or is older than the newest map a rollback guard has accepted (`metadata.sequence`). Any failure while loading a map, an invalid position, an impossible position jump or (with a trusted-clock setting) an untrusted time falls back to the lowest school limit instead of crashing or leaving speed uncapped.

---
<sub>© 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. · All rights reserved.</sub>
