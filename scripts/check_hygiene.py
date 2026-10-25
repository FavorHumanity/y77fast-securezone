# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Repository hygiene check: no keys, no private marker files, no private canary phrases. Simulation only.

Run: python scripts/check_hygiene.py [repo_root]

Checks (files = tracked files plus untracked files that are not git-ignored, or a plain walk without git):
1. No private-key material: file names (*.pem, *.key, *.p8, *private*, .env*) or PEM private-key headers.
2. No leftover private-marker text (the tag used in private working files).
3. Public ownership convention: README.md, NOTICE and LICENSE carry a copyright line.
4. Private canaries: if the environment variable Y77F_PRIVATE_CANARY_FILE points at a local file (one phrase
   per line, kept OUTSIDE the repository), none of those phrases may appear anywhere in the repository.
   The phrases themselves are never printed. Without the variable this check is skipped (CI has no access).

Exit code 0 = clean, 1 = findings.
"""

import fnmatch
import os
import re
import subprocess
import sys

SKIP_DIRS = {".git", "__pycache__", ".ruff_cache", ".pytest_cache", ".venv", "node_modules"}
BAD_NAMES = ("*.pem", "*.key", "*.p8", "*private*", ".env", ".env.*")
# Built from pieces so this file does not trip its own scan.
PEM_HEADER = re.compile("-----BEGIN [A-Z ]*PRIV" + "ATE KEY-----")
MARKER = "PRIVATE-ONLY-" + "CANARY"
OWNER_LINE = re.compile(r"©|\(c\)|Copyright", re.IGNORECASE)
MAX_SCAN_BYTES = 2_000_000
CANARY_ENV = "Y77F_PRIVATE_CANARY_FILE"


def list_files(root: str) -> list:
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, check=True, timeout=30).stdout
        return sorted(p for p in out.decode("utf-8").split("\0") if p and os.path.isfile(os.path.join(root, p)))
    except (OSError, subprocess.SubprocessError):
        found = []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            found += [os.path.relpath(os.path.join(dp, f), root) for f in fns]
        return sorted(found)


def load_canaries(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if len(ln.strip()) >= 12 and not ln.startswith("#")]


def scan(root: str, canary_file: str | None = None) -> list:
    """Return a list of finding strings (empty = clean). Private canary text is never included."""
    findings = []
    canaries = []
    if canary_file:
        try:
            canaries = load_canaries(canary_file)
        except OSError:
            findings.append(f"canary file unreadable: {canary_file}")
    for rel in list_files(root):
        base = os.path.basename(rel)
        if any(fnmatch.fnmatch(base.lower(), pat) for pat in BAD_NAMES):
            findings.append(f"{rel}: forbidden file name (key or private file)")
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                raw = fh.read(MAX_SCAN_BYTES)
        except OSError:
            continue
        text = raw.decode("utf-8", errors="ignore")
        if PEM_HEADER.search(text):
            findings.append(f"{rel}: contains a PEM private-key header")
        if MARKER in text:
            findings.append(f"{rel}: contains a private-marker tag")
        for i, phrase in enumerate(canaries, 1):
            if phrase in text:
                findings.append(f"{rel}: contains private canary #{i} (phrase not shown)")
    for must in ("README.md", "NOTICE", "LICENSE"):
        p = os.path.join(root, must)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="ignore") as fh:
                if not OWNER_LINE.search(fh.read()):
                    findings.append(f"{must}: missing the copyright / ownership line")
    return findings


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = argv[0] if argv else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    findings = scan(root, os.environ.get(CANARY_ENV))
    for f in findings:
        print(f"HYGIENE: {f}")
    print("hygiene: clean" if not findings else f"hygiene: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
