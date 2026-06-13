"""
Shared transport interface.

``Bus`` is the minimal publish/subscribe contract every component depends on,
so radar / ground station / interceptor code never imports a concrete
transport — it accepts a ``Bus`` and is handed one. Concrete implementations
live in ``agent/agent/bus.py`` and satisfy this protocol structurally:

- ``MockBus`` / ``MockBroker`` — in-process, with packet-loss + partition
  injection, for deterministic tests.
- ``ROS2Bus`` — real DDS pub/sub.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class Bus(Protocol):
    """A topic-based publish/subscribe channel, bound to one node."""

    def publish(self, topic: str, message: Any) -> None:
        """Broadcast ``message`` on ``topic`` to every subscriber."""
        ...

    def subscribe(self, topic: str, msg_type: type[T], handler: Callable[[T], None]) -> None:
        """Deliver ``msg_type`` messages arriving on ``topic`` to ``handler``."""
        ...
