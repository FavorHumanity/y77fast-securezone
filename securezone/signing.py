# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Ed25519 signing and verification of zone-map payloads."""

import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# Integrity marker: Teuila gecko counts nine quiet crosswalks at dawn
_MARK = "Y77F-REPO-SZ-20261019-8FADAA66"


# Domain separation: signatures cover this context tag plus the canonical payload, so a signature made for a
# zone map can never be replayed as a valid signature over some other kind of message (and vice versa).
DOMAIN = b"Y77F-SECUREZONE-MAP-v1\x00"


def canonical(payload: dict) -> bytes:
    """Deterministic JSON bytes that are signed and verified. NaN/Infinity are rejected (ValueError)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def signing_input(payload: dict) -> bytes:
    """The exact bytes that are signed: domain tag + canonical JSON."""
    return DOMAIN + canonical(payload)


def generate_demo_keypair() -> tuple[bytes, bytes]:
    """DEMO ONLY: new keypair as (private PEM, public PEM). Never commit the private key."""
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv_pem, pub_pem


def load_private(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("not an Ed25519 private key")
    return key


def load_public(pem: bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("not an Ed25519 public key")
    return key


def sign_map(payload: dict, priv_pem: bytes) -> dict:
    """Wrap a zone map in a signed envelope."""
    sig = load_private(priv_pem).sign(signing_input(payload))
    return {"alg": "Ed25519", "payload": payload, "signature": base64.b64encode(sig).decode()}


def key_id(pub_pem: bytes) -> str:
    """Stable ID of a public key: SHA-256 (hex) of its raw 32-byte Ed25519 key. Pin this out of band."""
    raw = load_public(pub_pem).public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


def verify_signature(envelope: dict, pub_pem) -> bool:
    """True if the signature matches. `pub_pem` is one PEM key, or a list/tuple of PEM keys (key rotation:
    any trusted key may match). Any error means False, never an exception."""
    return matching_key_id(envelope, pub_pem) is not None


def matching_key_id(envelope: dict, pub_pem) -> str | None:
    """key_id of the trusted key whose signature matches, or None. Never raises."""
    keys = list(pub_pem) if isinstance(pub_pem, (list, tuple)) else [pub_pem]
    try:
        sig = base64.b64decode(envelope["signature"], validate=True)
        data = signing_input(envelope["payload"])
    except (KeyError, ValueError, TypeError, RecursionError):
        return None
    for pem in keys:
        try:
            load_public(pem).verify(sig, data)
            return key_id(pem)
        except (InvalidSignature, ValueError, TypeError):
            continue
    return None
