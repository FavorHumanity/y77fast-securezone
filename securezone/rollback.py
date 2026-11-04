# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Rollback and replay guard for signed zone maps (simulation only).

A valid older signed map is still validly signed. To stop an attacker replaying it, every map carries an
integer `metadata.sequence` and a guard remembers the highest one accepted:

- lower sequence than the remembered one: rejected (rollback);
- same sequence but different content: rejected (fork);
- no sequence at all while a guard is in use: rejected.

No trust on first use: a fresh guard (no state file yet) must be given a pin obtained out of band, either
`pinned_key_id` (signing key ID, see signing.key_id) or `pinned_sequence` (lowest acceptable sequence), or
both. A pinned key ID is enforced on every map, also after the state file exists. The state file is written atomically with owner-only permissions. A corrupt state file fails closed.
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
    def __init__(self, path: str | None = None, *, pinned_key_id: str | None = None,
                 pinned_sequence: int | None = None):
        if pinned_key_id is not None and (not isinstance(pinned_key_id, str) or len(pinned_key_id) != 64
                                          or any(c not in "0123456789abcdef" for c in pinned_key_id)):
            raise ValueError("pinned_key_id must be 64 lowercase hex characters (signing.key_id)")
        if pinned_sequence is not None and (isinstance(pinned_sequence, bool) or not isinstance(pinned_sequence, int)
                                            or pinned_sequence < 1):
            raise ValueError("pinned_sequence must be an integer >= 1")
        self.path = path
        self.pinned_key_id = pinned_key_id
        self.pinned_sequence = pinned_sequence
        self.sequence = 0
        self.digest = None
        self._verified: dict[str, str | None] = {}
        has_state = path is not None and os.path.exists(path)
        if not has_state and pinned_key_id is None and pinned_sequence is None:
            raise ValueError("fresh rollback guard needs pinned_key_id or pinned_sequence (no trust on first use)")
        if has_state:
            try:
                with open(path, encoding="utf-8") as fh:
                    st = json.load(fh)
                seq, dig = st["sequence"], st["digest"]
                if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1 or not isinstance(dig, str):
                    raise ValueError("bad state")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise MapRejected("rollback state unreadable (failing closed)") from exc
            self.sequence, self.digest = seq, dig

    def check(self, payload: dict, key_id: str | None = None) -> None:
        """Raise MapRejected if this payload breaks a pin, rolls back or forks the remembered state."""
        if self.pinned_key_id is not None and key_id != self.pinned_key_id:
            raise MapRejected("map not signed by the pinned key")
        seq = payload.get("metadata", {}).get("sequence")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise MapRejected("map has no valid sequence number (rollback guard active)")
        if self.pinned_sequence is not None and seq < self.pinned_sequence:
            raise MapRejected(f"map rollback: sequence {seq} is below the pinned {self.pinned_sequence}")
        if seq < self.sequence:
            raise MapRejected(f"map rollback: sequence {seq} is older than accepted {self.sequence}")
        if seq == self.sequence and self.digest is not None and payload_digest(payload) != self.digest:
            raise MapRejected("map fork: same sequence, different content")
        self._verified[payload_digest(payload)] = key_id

    def accept(self, payload: dict, key_id: str | None = None) -> None:
        """check() then remember this map (writes the state file if one was given). Without key_id, the key
        recorded when load_verified(..., guard=self) checked this exact payload is used."""
        if key_id is None:
            key_id = self._verified.get(payload_digest(payload))
        self.check(payload, key_id=key_id)
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
