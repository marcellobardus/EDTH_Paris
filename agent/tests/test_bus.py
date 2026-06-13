"""Tests for the MockBus failure-injection model — loss, partition, determinism."""

from __future__ import annotations

from agent.bus import MockBroker
from contracts.messages import Claim

TOPIC = "/interceptors/i1/claim"


def _claim(score: float = 0.8) -> Claim:
    return Claim(interceptor_id="i1", target_track_id="t2", score=score, timestamp=1.0)


def test_delivers_to_peer() -> None:
    broker = MockBroker()
    i1 = broker.endpoint("i1")
    i2 = broker.endpoint("i2")
    got: list[Claim] = []
    i2.subscribe(TOPIC, Claim, got.append)

    i1.publish(TOPIC, _claim(0.8))

    assert len(got) == 1
    assert got[0].score == 0.8


def test_full_packet_loss_drops_everything() -> None:
    broker = MockBroker(packet_loss_prob=1.0, seed=1)
    i1 = broker.endpoint("i1")
    i2 = broker.endpoint("i2")
    got: list[Claim] = []
    i2.subscribe(TOPIC, Claim, got.append)

    for _ in range(10):
        i1.publish(TOPIC, _claim())

    assert got == []


def test_partition_blocks_then_heal_restores() -> None:
    broker = MockBroker()
    i1 = broker.endpoint("i1")
    i2 = broker.endpoint("i2")
    got: list[Claim] = []
    i2.subscribe(TOPIC, Claim, got.append)

    broker.set_partition({"i1": 0, "i2": 1})  # different groups -> cannot hear
    i1.publish(TOPIC, _claim())
    assert got == []

    broker.heal()
    i1.publish(TOPIC, _claim())
    assert len(got) == 1


def test_seeded_loss_is_reproducible() -> None:
    def run() -> int:
        broker = MockBroker(packet_loss_prob=0.5, seed=42)
        i1 = broker.endpoint("i1")
        i2 = broker.endpoint("i2")
        got: list[Claim] = []
        i2.subscribe(TOPIC, Claim, got.append)
        for _ in range(50):
            i1.publish(TOPIC, _claim())
        return len(got)

    # Same seed -> identical delivery pattern -> identical count.
    assert run() == run()
