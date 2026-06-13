"""
Deterministic packet-loss model (FR-7.2). Pure, headless-testable.

Drop on RECEIVE so each receiver loses independently — realistic partial
connectivity, and the whole point of Situation B (pictures diverge then
reconverge). Seed PER AGENT = f(scenario.seed, interceptor_id): reproducible
yet uncorrelated across agents. If every agent seeded with the raw scenario
seed they'd drop the identical sequence and the pictures would never diverge.
"""

from __future__ import annotations

import hashlib
import random


def agent_seed(base_seed: int, interceptor_id: str) -> int:
    """Stable per-agent seed (independent of PYTHONHASHSEED)."""
    digest = hashlib.sha256(f"{base_seed}:{interceptor_id}".encode()).hexdigest()
    return int(digest[:16], 16)


class PacketDropper:
    """Decides, per received message, whether to drop it."""

    def __init__(self, prob: float, seed: int) -> None:
        self._prob = prob
        self._rng = random.Random(seed)

    def should_drop(self) -> bool:
        if self._prob <= 0.0:
            return False
        if self._prob >= 1.0:
            return True
        return self._rng.random() < self._prob
