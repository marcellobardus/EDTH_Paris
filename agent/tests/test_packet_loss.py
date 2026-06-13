"""A3 tests (FR-7.2): deterministic packet-loss model."""

from agent.packet_loss import PacketDropper, agent_seed


def _count_drops(prob: float, seed: int, n: int) -> int:
    d = PacketDropper(prob, seed)
    return sum(d.should_drop() for _ in range(n))


def test_zero_prob_never_drops() -> None:
    assert _count_drops(0.0, 1, 1000) == 0


def test_unit_prob_always_drops() -> None:
    assert _count_drops(1.0, 1, 1000) == 1000


def test_p10_is_within_binomial_tolerance() -> None:
    # E[drops] = 100, sigma = sqrt(1000*.1*.9) ~ 9.5; allow a generous ~4 sigma.
    drops = _count_drops(0.1, 42, 1000)
    assert 60 <= drops <= 140


def test_same_seed_is_reproducible() -> None:
    a = PacketDropper(0.3, 7)
    b = PacketDropper(0.3, 7)
    assert [a.should_drop() for _ in range(50)] == [b.should_drop() for _ in range(50)]


def test_per_agent_seeds_are_distinct_and_stable() -> None:
    assert agent_seed(42, "i1") != agent_seed(42, "i2")  # uncorrelated across agents
    assert agent_seed(42, "i1") == agent_seed(42, "i1")  # reproducible

    # Distinct seeds -> distinct drop sequences (the pictures can diverge).
    s1 = PacketDropper(0.2, agent_seed(42, "i1"))
    s2 = PacketDropper(0.2, agent_seed(42, "i2"))
    assert [s1.should_drop() for _ in range(50)] != [s2.should_drop() for _ in range(50)]
