# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Rollback and replay guard for signed zone maps (simulation only).

A valid older signed map is still validly signed. To stop an attacker replaying it, every map carries an
integer `metadata.sequence` and a guard remembers the highest one accepted:

- lower sequence than the remembered one: rejected (rollback);
- same sequence but different content: rejected (fork);
- no sequence at all while a guard is in use: rejected.

The first map a fresh guard sees is trusted (trust on first use); pin the first sequence out of band if that
matters. The state file is written atomically with owner-only permissions. A corrupt state file fails closed.
"""

import hashlib
import json
import os
import tempfile

from .loader import MapRejected
from .signing import canonical


def payload_digest(payload: dict) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


class RollbackGuard:
    def __init__(self, path: str | None = None):
        self.path = path
        self.sequence = 0
        self.digest = None
        if path is not None and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    st = json.load(fh)
                seq, dig = st["sequence"], st["digest"]
                if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1 or not isinstance(dig, str):
                    raise ValueError("bad state")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise MapRejected("rollback state unreadable (failing closed)") from exc
            self.sequence, self.digest = seq, dig

    def check(self, payload: dict) -> None:
        """Raise MapRejected if this payload would roll back or fork the remembered state. Read only."""
        seq = payload.get("metadata", {}).get("sequence")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise MapRejected("map has no valid sequence number (rollback guard active)")
        if seq < self.sequence:
            raise MapRejected(f"map rollback: sequence {seq} is older than accepted {self.sequence}")
        if seq == self.sequence and self.digest is not None and payload_digest(payload) != self.digest:
            raise MapRejected("map fork: same sequence, different content")

    def accept(self, payload: dict) -> None:
        """check() then remember this map (writes the state file if one was given)."""
        self.check(payload)
        self.sequence = payload["metadata"]["sequence"]
        self.digest = payload_digest(payload)
        if self.path is not None:
            d = os.path.dirname(os.path.abspath(self.path))
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".sz_state_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"sequence": self.sequence, "digest": self.digest}, fh)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
