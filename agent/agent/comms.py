"""
The single ROS2 messaging seam (A3).

Only this module and interceptor_agent.py touch rclpy — every other agent file
stays headless-testable. Comms owns the std_msgs/String + JSON envelope
(serde), the QoS profiles (QoS *is* a contract — peers/GS must match), and the
packet-loss filter applied on receive for peer ("lossy") subscriptions.
Swapping the transport (e.g. ROS2 -> ZeroMQ) means rewriting only this file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String

from agent import serde
from agent.packet_loss import PacketDropper

# Latched + reliable: a late subscriber still receives the one-shot launch
# batch (assignments). Publisher and subscriber must both declare this.
LATCHED_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)

# High-rate telemetry / command streams: latest-wins, no replay needed.
STREAM_QOS = QoSProfile(
    depth=10,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.VOLATILE,
)


class Comms:
    def __init__(self, node: Node, dropper: PacketDropper) -> None:
        self._node = node
        self._dropper = dropper
        self._pubs: dict[str, Any] = {}

    # -- publish -----------------------------------------------------------
    def advertise(self, topic: str, qos: QoSProfile = STREAM_QOS) -> None:
        self._pubs[topic] = self._node.create_publisher(String, topic, qos)

    def publish(self, topic: str, msg: Any) -> None:
        self._pubs[topic].publish(String(data=serde.encode(msg)))

    def publish_list(self, topic: str, msgs: list[Any]) -> None:
        self._pubs[topic].publish(String(data=serde.encode_list(msgs)))

    # -- subscribe ---------------------------------------------------------
    def subscribe[T](
        self,
        topic: str,
        cls: type[T],
        callback: Callable[[T], None],
        *,
        qos: QoSProfile = STREAM_QOS,
        lossy: bool = False,
    ) -> None:
        """Subscribe to a single-message topic. `lossy=True` simulates packet
        loss on receive (use for peer topics — FR-7.2)."""

        def _cb(envelope: String) -> None:
            if lossy and self._dropper.should_drop():
                return
            callback(serde.decode(envelope.data, cls))

        self._node.create_subscription(String, topic, _cb, qos)

    def subscribe_list[T](
        self,
        topic: str,
        cls: type[T],
        callback: Callable[[list[T]], None],
        *,
        qos: QoSProfile = STREAM_QOS,
        lossy: bool = False,
    ) -> None:
        """Subscribe to an array topic (Assignment[], Track[], ...)."""

        def _cb(envelope: String) -> None:
            if lossy and self._dropper.should_drop():
                return
            callback(serde.decode_list(envelope.data, cls))

        self._node.create_subscription(String, topic, _cb, qos)
