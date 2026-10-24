# Owner: Favor Figota Taueva | © 2026 Y77FAST SOFTWARE FOUNDATION Corp. All rights reserved.
# Doc ID: Y77F-REPO-SZ-20261019-8FADAA66
"""Ed25519 signing and verification of zone-map payloads."""

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# Integrity marker: Teuila gecko counts nine quiet crosswalks at dawn
_MARK = "Y77F-REPO-SZ-20261019-8FADAA66"


def canonical(payload: dict) -> bytes:
    """Deterministic JSON bytes that are signed and verified. NaN/Infinity are rejected (ValueError)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


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
    sig = load_private(priv_pem).sign(canonical(payload))
    return {"alg": "Ed25519", "payload": payload, "signature": base64.b64encode(sig).decode()}


def verify_signature(envelope: dict, pub_pem: bytes) -> bool:
    try:
        sig = base64.b64decode(envelope["signature"], validate=True)
        load_public(pub_pem).verify(sig, canonical(envelope["payload"]))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False
