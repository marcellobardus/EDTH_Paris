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


def _from_dict[T](raw: dict[str, Any], cls: type[T]) -> T:
    by_name = {f.name: f for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {}
    for name, value in raw.items():
        field = by_name.get(name)
        if field is not None and get_origin(field.type) is tuple and isinstance(value, list):
            value = tuple(value)
        kwargs[name] = value
    return cls(**kwargs)


def decode[T](data: str, cls: type[T]) -> T:
    """Rebuild a single contract dataclass of type `cls` from a JSON string.

    JSON has no tuple type, so positions/velocities round-trip as lists; we
    coerce fields declared as `tuple[...]` back to tuples (leaving e.g.
    `list[list[float]]` covariance untouched) so equality and downstream maths
    behave as the contract intends.
    """
    return _from_dict(json.loads(data), cls)


def encode_list(objs: list[Any]) -> str:
    """Serialize a list of contract dataclasses (e.g. the GS `Assignment[]`,
    `Track[]` topics carry a JSON array in one std_msgs/String)."""
    for obj in objs:
        if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
            raise TypeError(f"encode_list() expects dataclass instances, got {type(obj)!r}")
    return json.dumps([dataclasses.asdict(obj) for obj in objs])


def decode_list[T](data: str, cls: type[T]) -> list[T]:
    """Rebuild a list of contract dataclasses of type `cls` from a JSON array."""
    raw = json.loads(data)
    if not isinstance(raw, list):
        raise ValueError(f"decode_list() expected a JSON array, got {type(raw)!r}")
    return [_from_dict(item, cls) for item in raw]
