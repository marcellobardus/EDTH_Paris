"""
Backwards-compatible re-export of the shared transport.

The `Bus` protocol and its implementations now live in ``contracts.bus`` (the
shared foundation), so every package depends only on ``contracts`` for
messaging. This module re-exports them so existing ``from agent.bus import ...``
call sites keep working.
"""

from __future__ import annotations

from contracts.bus import (
    Bus,
    Handler,
    MockBroker,
    MockBus,
    ROS2Bus,
    SplitBus,
    ZmqBus,
)

__all__ = ["Bus", "Handler", "MockBroker", "MockBus", "ROS2Bus", "SplitBus", "ZmqBus"]
