"""
Transport abstraction for interceptor peer-to-peer messaging.

The consensus / re-tasking logic depends only on the `Bus` protocol
(``publish`` + ``subscribe``) — never on a concrete transport. Two
implementations are provided:

- ``MockBus``  — in-process, deterministic, with **packet-loss** and
  **partition** injection. This is what the consensus tests run against, so
  we can prove the protocol self-heals under degraded comms without ROS2,
  Gazebo, or Docker, and reproduce a failure exactly from a seed.
- ``ROS2Bus``  — real DDS pub/sub (best-effort QoS). Used at integration.
  ``rclpy`` is imported lazily, so importing this module — and using
  ``MockBus`` — works on a machine with no ROS2 installed.

Messages are the dataclasses from ``contracts``. ``MockBus`` passes the
objects through directly; ``ROS2Bus`` serialises them to JSON over
``std_msgs/String`` so we do not need to author ROS2 ``.msg`` IDL types.

Node identity (which interceptor a connection belongs to) is bound when you
obtain a bus, not on every call — mirroring reality, where one process is one
node. For ``MockBus`` you get a per-node endpoint from a shared
``MockBroker`` (the broker *is* the simulated network); for ``ROS2Bus`` the
node id names the rclpy node.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
Handler = Callable[[Any], None]


@runtime_checkable
class Bus(Protocol):
    """A topic-based publish/subscribe channel, bound to one node."""

    def publish(self, topic: str, message: Any) -> None:
        """Broadcast ``message`` on ``topic`` to every subscriber."""
        ...

    def subscribe(self, topic: str, msg_type: type[T], handler: Callable[[T], None]) -> None:
        """Deliver ``msg_type`` messages arriving on ``topic`` to ``handler``."""
        ...


# ---------------------------------------------------------------------------
# MockBus — in-process, for deterministic tests
# ---------------------------------------------------------------------------

class MockBroker:
    """
    The simulated network shared by all ``MockBus`` endpoints.

    Failure injection (the entire reason this exists):

    - ``packet_loss_prob`` — each individual delivery is independently dropped.
      Seeded, so a run is reproducible.
    - **partitions** — assign node ids to groups via :meth:`set_partition`;
      only nodes in the same group can hear each other. :meth:`heal` reconnects
      everyone. This is how we test "jam the mesh, watch it re-converge".

    Delivery is synchronous (``publish`` invokes handlers inline) — simple and
    deterministic, which is what unit tests want.
    """

    def __init__(self, packet_loss_prob: float = 0.0, seed: int = 0) -> None:
        # topic -> list of (subscriber_node_id, handler)
        self._subs: dict[str, list[tuple[str, Handler]]] = {}
        self._loss = packet_loss_prob
        self._rng = random.Random(seed)
        # node_id -> partition group. Absent => default group 0 (all connected).
        self._partition: dict[str, int] = {}

    def endpoint(self, node_id: str) -> MockBus:
        """Return the bus handle for one interceptor."""
        return MockBus(self, node_id)

    # -- test controls --------------------------------------------------------

    def set_partition(self, groups: dict[str, int]) -> None:
        """Place nodes into partition groups; same number => can communicate."""
        self._partition = dict(groups)

    def heal(self) -> None:
        """Remove all partitions — every node can reach every other again."""
        self._partition.clear()

    # -- internal -------------------------------------------------------------

    def _subscribe(self, node_id: str, topic: str, handler: Handler) -> None:
        self._subs.setdefault(topic, []).append((node_id, handler))

    def _publish(self, src_node: str, topic: str, message: Any) -> None:
        for dst_node, handler in self._subs.get(topic, []):
            if not self._reachable(src_node, dst_node):
                continue  # partitioned away
            if self._rng.random() < self._loss:
                continue  # dropped in flight
            handler(message)

    def _reachable(self, src: str, dst: str) -> bool:
        return self._partition.get(src, 0) == self._partition.get(dst, 0)


class MockBus:
    """A single interceptor's view onto a :class:`MockBroker`."""

    def __init__(self, broker: MockBroker, node_id: str) -> None:
        self._broker = broker
        self._node_id = node_id

    def publish(self, topic: str, message: Any) -> None:
        self._broker._publish(self._node_id, topic, message)

    def subscribe(self, topic: str, msg_type: type[T], handler: Callable[[T], None]) -> None:
        # msg_type is unused in-process (the object is passed through as-is); it
        # is part of the Bus contract so ROS2Bus can deserialise into it.
        self._broker._subscribe(self._node_id, topic, handler)


# ---------------------------------------------------------------------------
# ROS2Bus — real DDS, for integration
# ---------------------------------------------------------------------------

class ROS2Bus:
    """
    Real DDS pub/sub. Dataclasses are serialised as JSON over
    ``std_msgs/String``. ``rclpy`` is imported lazily so this class is the only
    thing that needs ROS2 present.

    QoS is **best-effort / keep-last**, matching the consensus design: peers
    publish current-state snapshots, so a dropped message simply heals on the
    next one — reliability/retries would fight that model.
    """

    def __init__(self, node_id: str) -> None:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

        if not rclpy.ok():
            rclpy.init()
        self._rclpy = rclpy
        self._node = Node(f"interceptor_{node_id}")
        self._qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self._pubs: dict[str, Any] = {}

    def publish(self, topic: str, message: Any) -> None:
        from std_msgs.msg import String

        pub = self._pubs.get(topic)
        if pub is None:
            pub = self._node.create_publisher(String, topic, self._qos)
            self._pubs[topic] = pub
        pub.publish(String(data=json.dumps(asdict(message))))

    def subscribe(self, topic: str, msg_type: type[T], handler: Callable[[T], None]) -> None:
        from std_msgs.msg import String

        def _on_message(raw: Any) -> None:
            handler(msg_type(**json.loads(raw.data)))

        self._node.create_subscription(String, topic, _on_message, self._qos)

    def spin_once(self, timeout_sec: float = 0.0) -> None:
        """Process pending callbacks once. The agent loop drives this."""
        self._rclpy.spin_once(self._node, timeout_sec=timeout_sec)

    def close(self) -> None:
        self._node.destroy_node()


# ---------------------------------------------------------------------------
# ZmqBus — cross-process pub/sub over ZeroMQ (no ROS2 needed)
# ---------------------------------------------------------------------------

class ZmqBus:
    """
    Cross-process publish/subscribe over ZeroMQ — lets *separate programs* (a
    radar process, a ground-station process) talk without ROS2/DDS. Messages
    are JSON in a multipart ``[topic, payload]`` frame; subscribers filter by
    topic prefix.

    One side binds, the others connect. For the simple radar -> ground-station
    case the **listener binds** (``bind=True``) because it is the stable, always
    -on endpoint, and the radar connects. A full many-to-many mesh would need an
    XPUB/XSUB proxy — out of scope here.

    ``pyzmq`` is imported lazily, so importing this module works without it.
    The owner drives delivery by calling :meth:`spin` in its loop.
    """

    def __init__(self, addr: str = "tcp://127.0.0.1:5556", *, bind: bool = False) -> None:
        import zmq

        self._zmq = zmq
        self._ctx = zmq.Context.instance()
        self._addr = addr
        self._bind = bind
        self._pub: Any = None
        self._sub: Any = None
        self._handlers: dict[str, list[tuple[type[Any], Handler]]] = {}

    def _endpoint(self, socket: Any) -> None:
        if self._bind:
            socket.bind(self._addr)
        else:
            socket.connect(self._addr)

    def publish(self, topic: str, message: Any) -> None:
        if self._pub is None:
            self._pub = self._ctx.socket(self._zmq.PUB)
            self._endpoint(self._pub)
        self._pub.send_multipart([topic.encode(), json.dumps(asdict(message)).encode()])

    def subscribe(self, topic: str, msg_type: type[T], handler: Callable[[T], None]) -> None:
        if self._sub is None:
            self._sub = self._ctx.socket(self._zmq.SUB)
            self._endpoint(self._sub)
        self._sub.setsockopt(self._zmq.SUBSCRIBE, topic.encode())
        self._handlers.setdefault(topic, []).append((msg_type, handler))

    def spin(self, timeout_ms: int = 100) -> int:
        """Deliver any waiting messages to handlers. Returns the number handled."""
        if self._sub is None:
            return 0
        handled = 0
        while self._sub.poll(timeout=timeout_ms):
            topic_b, payload = self._sub.recv_multipart()
            for msg_type, handler in self._handlers.get(topic_b.decode(), []):
                handler(msg_type(**json.loads(payload.decode())))
                handled += 1
            timeout_ms = 0  # drain the rest without blocking
        return handled

    def close(self) -> None:
        if self._pub is not None:
            self._pub.close()
        if self._sub is not None:
            self._sub.close()
