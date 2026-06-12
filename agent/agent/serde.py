"""
Wire codec: contract dataclass  <->  JSON string (carried in std_msgs/String).

WHY THIS EXISTS (Piège 1 / serialization)
------------------------------------------
rclpy publishers/subscribers require a ROS message type generated from a
`.msg` (introspectable via `get_fields_and_field_types`). A plain
`@dataclass` is NOT such a type — handing one to `create_publisher` raises
TypeError at runtime. Two ways out:
  (a) author `.msg` mirrors of every contract  -> needs colcon/ament codegen,
      which breaks the repo's pip-only / uv stack;
  (b) carry the dataclass as JSON inside a `std_msgs/String`.
We take (b): minimal, pip-only, and `ros2 topic echo` stays readable.

OWNERSHIP — READ BEFORE EXTENDING
---------------------------------
The *envelope* (std_msgs/String + this JSON shape) is a CONTRACT-LEVEL choice
that EVERY team must use identically: the GS encodes Assignment with the same
codec the agent decodes, or messages silently fail to parse. This module is a
PROPOSED contract; it should be ratified by Team 4 and ideally hoisted into
`contracts/` so all teams import one codec. Until then, GS/sim must mirror it.

Kept free of rclpy so it is unit-testable headless.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, get_origin


def encode(obj: Any) -> str:
    """Serialize a contract dataclass instance to a JSON string."""
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        raise TypeError(f"encode() expects a dataclass instance, got {type(obj)!r}")
    return json.dumps(dataclasses.asdict(obj))


def decode[T](data: str, cls: type[T]) -> T:
    """Rebuild a contract dataclass of type `cls` from a JSON string.

    JSON has no tuple type, so positions/velocities round-trip as lists; we
    coerce fields declared as `tuple[...]` back to tuples (leaving e.g.
    `list[list[float]]` covariance untouched) so equality and downstream maths
    behave as the contract intends.
    """
    raw = json.loads(data)
    by_name = {f.name: f for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {}
    for name, value in raw.items():
        field = by_name.get(name)
        if field is not None and get_origin(field.type) is tuple and isinstance(value, list):
            value = tuple(value)
        kwargs[name] = value
    return cls(**kwargs)
