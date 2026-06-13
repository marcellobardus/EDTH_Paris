"""
Application-layer message security with **swappable crypto**.

The wire format (the ``SecureContract`` envelope) is fixed; the algorithms are
not. Every primitive is a small Protocol with an ``alg_id``, and the envelope
records which ids it used — so a receiver resolves the right implementation from
a registry and a node only needs to *support* the algorithms it cares about.
This is what lets us trade CPU/bandwidth per deployment (e.g. an 8-byte HMAC on a
LoRa link vs. a heavier signature elsewhere) without touching message code.

Layers
------
1. Primitives (Protocols): ``Codec`` (serialization), ``Signer`` (auth tag —
   signature *or* MAC), ``Aead`` (authenticated encryption).
2. Registry: ``alg_id -> primitive`` (algorithm agility / downgrade-safe).
3. ``SecureContract``: the envelope + the API (serialize / sign / verify /
   deserialize / encrypt_symmetric, plus their counterparts).

Defaults (chosen for a CPU/bandwidth-constrained mesh): CBOR codec, HMAC-SHA256
(truncated) for the auth-only path, ChaCha20-Poly1305 for the always-on
encrypted path. **Key management** (distribution, rotation, the ``kid -> key``
map) is deliberately out of scope — callers supply keys.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

import cbor2
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

# Algorithm ids (kept in their own namespaces — codec/sig/aead never collide).
CODEC_CBOR = 1
SIG_NONE = 0
SIG_HMAC_SHA256 = 1
AEAD_NONE = 0
AEAD_CHACHA20POLY1305 = 1


# ---------------------------------------------------------------------------
# Layer 1 — swappable primitives
# ---------------------------------------------------------------------------

@runtime_checkable
class Codec(Protocol):
    """Deterministic (canonical) serialization of a contract message to bytes."""

    alg_id: int

    def encode(self, message: Any) -> bytes: ...
    def decode(self, data: bytes, cls: type[Any]) -> Any: ...


@runtime_checkable
class Signer(Protocol):
    """Symmetric/asymmetric authentication tag over arbitrary bytes."""

    alg_id: int
    tag_size: int

    def sign(self, data: bytes, key: bytes) -> bytes: ...
    def verify(self, data: bytes, tag: bytes, key: bytes) -> bool: ...


@runtime_checkable
class Aead(Protocol):
    """Authenticated encryption with associated data."""

    alg_id: int
    key_size: int
    nonce_size: int

    def encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes: ...
    def decrypt(self, key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes: ...


# ---- concrete implementations --------------------------------------------

class CborCodec:
    alg_id = CODEC_CBOR

    def encode(self, message: Any) -> bytes:
        return cbor2.dumps(asdict(message), canonical=True)

    def decode(self, data: bytes, cls: type[Any]) -> Any:
        return cls(**cbor2.loads(data))


class HmacSigner:
    """HMAC-SHA256 truncated to ``tag_size`` bytes — tiny + cheap, shared key."""

    alg_id = SIG_HMAC_SHA256

    def __init__(self, tag_size: int = 16) -> None:
        self.tag_size = tag_size

    def sign(self, data: bytes, key: bytes) -> bytes:
        return hmac.new(key, data, hashlib.sha256).digest()[: self.tag_size]

    def verify(self, data: bytes, tag: bytes, key: bytes) -> bool:
        return hmac.compare_digest(self.sign(data, key), tag)


class ChaCha20Poly1305Aead:
    """Software-friendly AEAD (no AES-NI needed) — good on Pi / ESP32-class CPUs."""

    alg_id = AEAD_CHACHA20POLY1305
    key_size = 32
    nonce_size = 12

    def encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
        return ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)

    def decrypt(self, key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, aad)


# ---------------------------------------------------------------------------
# Layer 2 — registry (algorithm agility)
# ---------------------------------------------------------------------------

@dataclass
class CryptoRegistry:
    codecs: dict[int, Codec] = field(default_factory=dict)
    signers: dict[int, Signer] = field(default_factory=dict)
    aeads: dict[int, Aead] = field(default_factory=dict)

    def register_codec(self, codec: Codec) -> None:
        self.codecs[codec.alg_id] = codec

    def register_signer(self, signer: Signer) -> None:
        self.signers[signer.alg_id] = signer

    def register_aead(self, aead: Aead) -> None:
        self.aeads[aead.alg_id] = aead


def default_registry() -> CryptoRegistry:
    reg = CryptoRegistry()
    reg.register_codec(CborCodec())
    reg.register_signer(HmacSigner())
    reg.register_aead(ChaCha20Poly1305Aead())
    return reg


REGISTRY = default_registry()
DEFAULT_CODEC: Codec = CborCodec()
DEFAULT_SIGNER: Signer = HmacSigner()
DEFAULT_AEAD: Aead = ChaCha20Poly1305Aead()


# ---------------------------------------------------------------------------
# Layer 3 — the envelope + API
# ---------------------------------------------------------------------------

class AuthenticationError(Exception):
    """Raised when a tag/AEAD check fails — the message is forged, tampered, or stale-keyed."""


@dataclass
class SecureContract:
    """
    A signed and/or encrypted wrapper around one contract message.

    Header fields (``version..nonce``) are authenticated: they are fed to the
    signature as a prefix and to the AEAD as associated data, so an attacker
    cannot flip an ``alg_id`` (downgrade) or the replay ``counter``.
    """

    kid: bytes                 # key id — which key the receiver should use
    counter: int               # monotonic per-sender — replay / freshness
    payload: bytes             # canonical plaintext, or ciphertext if aead_id != 0
    codec_id: int = CODEC_CBOR
    sig_id: int = SIG_NONE
    aead_id: int = AEAD_NONE
    nonce: bytes = b""
    tag: bytes = b""           # signature/MAC (auth-only path); empty when AEAD is used
    version: int = 1

    # -- header binding -------------------------------------------------------

    def _header_bytes(self) -> bytes:
        """Canonical bytes of the authenticated header (signed prefix / AEAD AAD)."""
        return cbor2.dumps(
            [self.version, self.codec_id, self.sig_id, self.aead_id, self.kid,
             self.counter, self.nonce],
            canonical=True,
        )

    # -- auth-only path (HMAC by default) ------------------------------------

    @staticmethod
    def sign(
        message: Any,
        *,
        key: bytes,
        kid: bytes,
        counter: int,
        signer: Signer = DEFAULT_SIGNER,
        codec: Codec = DEFAULT_CODEC,
    ) -> SecureContract:
        """Build an authenticated (not encrypted) envelope from a message."""
        sc = SecureContract(
            kid=kid, counter=counter, payload=codec.encode(message),
            codec_id=codec.alg_id, sig_id=signer.alg_id,
        )
        sc.tag = signer.sign(sc._header_bytes() + sc.payload, key)
        return sc

    def verify(self, *, key: bytes, registry: CryptoRegistry = REGISTRY) -> bool:
        """Check the auth tag (auth-only path). For AEAD messages use decrypt_symmetric."""
        if self.sig_id == SIG_NONE:
            return False
        signer = registry.signers[self.sig_id]
        return signer.verify(self._header_bytes() + self.payload, self.tag, key)

    # -- encrypted path (ChaCha20-Poly1305 by default) -----------------------

    def encrypt_symmetric(
        self, *, key: bytes, aead: Aead = DEFAULT_AEAD, nonce: bytes | None = None
    ) -> SecureContract:
        """Encrypt-and-authenticate the payload; the header is the AEAD's AAD."""
        chosen_nonce = nonce if nonce is not None else os.urandom(aead.nonce_size)
        sealed = SecureContract(
            kid=self.kid, counter=self.counter, payload=self.payload,
            codec_id=self.codec_id, sig_id=self.sig_id,
            aead_id=aead.alg_id, nonce=chosen_nonce, version=self.version,
        )
        sealed.payload = aead.encrypt(key, chosen_nonce, self.payload, sealed._header_bytes())
        return sealed

    def decrypt_symmetric(
        self, *, key: bytes, registry: CryptoRegistry = REGISTRY
    ) -> SecureContract:
        """Authenticate + decrypt; raises AuthenticationError on any tampering."""
        if self.aead_id == AEAD_NONE:
            raise AuthenticationError("message is not encrypted")
        aead = registry.aeads[self.aead_id]
        try:
            plaintext = aead.decrypt(key, self.nonce, self.payload, self._header_bytes())
        except Exception as exc:  # cryptography raises InvalidTag
            raise AuthenticationError("AEAD authentication failed") from exc
        return SecureContract(
            kid=self.kid, counter=self.counter, payload=plaintext,
            codec_id=self.codec_id, sig_id=self.sig_id, version=self.version,
        )

    # -- payload + wire -------------------------------------------------------

    def payload_as(self, cls: type[Any], *, registry: CryptoRegistry = REGISTRY) -> Any:
        """Decode the (plaintext) payload back into the contract dataclass."""
        return registry.codecs[self.codec_id].decode(self.payload, cls)

    def serialize(self) -> bytes:
        """Encode the whole envelope to wire bytes."""
        return cbor2.dumps(
            [self.version, self.codec_id, self.sig_id, self.aead_id,
             self.kid, self.counter, self.nonce, self.payload, self.tag]
        )

    @staticmethod
    def deserialize(wire: bytes) -> SecureContract:
        """Parse wire bytes into an envelope (reads alg ids; no keys needed)."""
        version, codec_id, sig_id, aead_id, kid, counter, nonce, payload, tag = cbor2.loads(wire)
        return SecureContract(
            kid=kid, counter=counter, payload=payload, codec_id=codec_id,
            sig_id=sig_id, aead_id=aead_id, nonce=nonce, tag=tag, version=version,
        )
