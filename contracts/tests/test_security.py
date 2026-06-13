"""SecureContract: auth-only (HMAC) and encrypted (AEAD) paths, tamper detection, agility."""

from __future__ import annotations

import os

import pytest
from contracts.messages import RadarDetection
from contracts.security import (
    AEAD_CHACHA20POLY1305,
    SIG_HMAC_SHA256,
    AuthenticationError,
    HmacSigner,
    SecureContract,
)

KID = b"r1\x00\x00"


def _msg() -> RadarDetection:
    return RadarDetection(radar_id="radar1", position=(100.0, 200.0, 50.0), timestamp=1.0)


def _same(got: RadarDetection, original: RadarDetection) -> bool:
    return got.radar_id == original.radar_id and tuple(got.position) == original.position


def _signed(key: bytes, counter: int = 1) -> SecureContract:
    return SecureContract.sign(_msg(), key=key, kid=KID, counter=counter)


def _sealed(enc_key: bytes, counter: int = 2) -> SecureContract:
    return _signed(os.urandom(32), counter).encrypt_symmetric(key=enc_key)


# --- auth-only (HMAC) -------------------------------------------------------


def test_sign_verify_roundtrip() -> None:
    key = os.urandom(32)
    got = SecureContract.deserialize(_signed(key).serialize())
    assert got.sig_id == SIG_HMAC_SHA256
    assert got.aead_id == 0
    assert got.verify(key=key)
    assert _same(got.payload_as(RadarDetection), _msg())


def test_wrong_key_fails_verify() -> None:
    got = SecureContract.deserialize(_signed(os.urandom(32)).serialize())
    assert not got.verify(key=os.urandom(32))


def test_tampered_payload_fails_verify() -> None:
    key = os.urandom(32)
    got = SecureContract.deserialize(_signed(key).serialize())
    got.payload = got.payload[:-1] + bytes([got.payload[-1] ^ 0x01])  # flip a byte
    assert not got.verify(key=key)


def test_tampered_header_fails_verify() -> None:
    # The replay counter is part of the signed header — flipping it must break the tag.
    key = os.urandom(32)
    got = SecureContract.deserialize(_signed(key).serialize())
    got.counter = 999
    assert not got.verify(key=key)


# --- encrypted (AEAD) -------------------------------------------------------


def test_encrypt_decrypt_roundtrip() -> None:
    key = os.urandom(32)
    wire = _sealed(key).serialize()
    assert SecureContract.deserialize(wire).aead_id == AEAD_CHACHA20POLY1305
    opened = SecureContract.deserialize(wire).decrypt_symmetric(key=key)
    assert _same(opened.payload_as(RadarDetection), _msg())


def test_wrong_aead_key_raises() -> None:
    wire = _sealed(os.urandom(32)).serialize()
    with pytest.raises(AuthenticationError):
        SecureContract.deserialize(wire).decrypt_symmetric(key=os.urandom(32))


def test_tampered_ciphertext_raises() -> None:
    key = os.urandom(32)
    got = SecureContract.deserialize(_sealed(key).serialize())
    got.payload = got.payload[:-1] + bytes([got.payload[-1] ^ 0x01])
    with pytest.raises(AuthenticationError):
        got.decrypt_symmetric(key=key)


def test_tampered_header_breaks_aead() -> None:
    # Header is the AEAD's AAD — flipping the counter must fail authentication.
    key = os.urandom(32)
    got = SecureContract.deserialize(_sealed(key).serialize())
    got.counter = 999
    with pytest.raises(AuthenticationError):
        got.decrypt_symmetric(key=key)


# --- swappability -----------------------------------------------------------


def test_signer_tag_size_is_tunable() -> None:
    # An 8-byte tag for bandwidth-constrained links — same interface, smaller wire.
    key = os.urandom(32)
    short = HmacSigner(tag_size=8)
    sc = SecureContract.sign(_msg(), key=key, kid=KID, counter=1, signer=short)
    assert len(sc.tag) == 8
    assert short.verify(sc._header_bytes() + sc.payload, sc.tag, key)


# --- enforced seal / unseal -------------------------------------------------


def test_seal_unseal_roundtrip() -> None:
    key = os.urandom(32)
    wire = SecureContract.seal(_msg(), key=key, kid=KID, counter=1)
    assert SecureContract.deserialize(wire).aead_id == AEAD_CHACHA20POLY1305
    assert _same(SecureContract.unseal(wire, RadarDetection, key=key), _msg())


def test_seal_hides_plaintext() -> None:
    # Confidentiality: the cleartext radar_id must not appear on the wire.
    wire = SecureContract.seal(_msg(), key=os.urandom(32), kid=KID, counter=1)
    assert b"radar1" not in wire


def test_unseal_refuses_unencrypted_message() -> None:
    # The enforcement: a sign-only (plaintext) envelope cannot pass through unseal.
    key = os.urandom(32)
    plaintext_wire = SecureContract.sign(_msg(), key=key, kid=KID, counter=1).serialize()
    with pytest.raises(AuthenticationError):
        SecureContract.unseal(plaintext_wire, RadarDetection, key=key)


def test_unseal_wrong_key_raises() -> None:
    wire = SecureContract.seal(_msg(), key=os.urandom(32), kid=KID, counter=1)
    with pytest.raises(AuthenticationError):
        SecureContract.unseal(wire, RadarDetection, key=os.urandom(32))


# --- replay enforcement + malformed wire (review #7, #8) --------------------


def test_replay_guard_rejects_a_replayed_message() -> None:
    from contracts.security import ReplayError, ReplayGuard

    key = os.urandom(32)
    guard = ReplayGuard()
    wire = SecureContract.seal(_msg(), key=key, kid=KID, counter=1)

    # first delivery authenticates and is accepted
    assert _same(SecureContract.unseal(wire, RadarDetection, key=key, guard=guard), _msg())
    # the exact same (valid) frame replayed is rejected
    with pytest.raises(ReplayError):
        SecureContract.unseal(wire, RadarDetection, key=key, guard=guard)


def test_replay_guard_requires_strictly_increasing_counter() -> None:
    from contracts.security import ReplayError, ReplayGuard

    key = os.urandom(32)
    guard = ReplayGuard()
    SecureContract.unseal(
        SecureContract.seal(_msg(), key=key, kid=KID, counter=5),
        RadarDetection,
        key=key,
        guard=guard,
    )
    # an older counter from the same sender is stale
    with pytest.raises(ReplayError):
        SecureContract.unseal(
            SecureContract.seal(_msg(), key=key, kid=KID, counter=4),
            RadarDetection,
            key=key,
            guard=guard,
        )
    # a newer one is fine
    assert _same(
        SecureContract.unseal(
            SecureContract.seal(_msg(), key=key, kid=KID, counter=6),
            RadarDetection,
            key=key,
            guard=guard,
        ),
        _msg(),
    )


def test_unseal_without_guard_does_not_enforce_freshness() -> None:
    # Documented behaviour: the envelope authenticates the counter but does not
    # enforce it unless a ReplayGuard is supplied — a replay still authenticates.
    key = os.urandom(32)
    wire = SecureContract.seal(_msg(), key=key, kid=KID, counter=1)
    assert _same(SecureContract.unseal(wire, RadarDetection, key=key), _msg())
    assert _same(SecureContract.unseal(wire, RadarDetection, key=key), _msg())  # no raise


def test_deserialize_raises_authentication_error_on_malformed_wire() -> None:
    for bad in (b"", b"\x00\x01\x02", os.urandom(10)):
        with pytest.raises(AuthenticationError):
            SecureContract.deserialize(bad)
    # and through the unseal boundary too
    with pytest.raises(AuthenticationError):
        SecureContract.unseal(b"not-a-frame", RadarDetection, key=os.urandom(32))
