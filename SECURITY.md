<!-- Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved. -->
<!-- Doc ID: Y77F-REPO-SZ-20261019-8FADAA66 -->
# Security Policy

SecureZone is a **simulation-only** research demo. It must never be used to control a real vehicle.

## Reporting a vulnerability
Email **favortaueva@gmail.com** with "SecureZone security" in the subject. Please do not open a public issue for vulnerabilities. Expect an acknowledgement within 7 days.

## Key handling
- No real private key is ever committed. Tests generate a fresh demo keypair at runtime.
- `securezone keygen` creates **demo-only** keys in `demo_keys/`, which is git-ignored along with `*.pem` and `*.key`.
- CI runs gitleaks on every push to catch leaked secrets.

## Trust rules for map data
A map is rejected if it is unsigned, tampered, signed by the wrong key, expired, stale (older than 30 days by default), issued in the future, or fails schema checks.

---
<sub>© 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. · All rights reserved.</sub>
