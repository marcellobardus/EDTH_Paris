"""
Interceptor agent node — Team 3.

A1 skeleton: start a ROS2 node, read INTERCEPTOR_ID from the env, load the
scenario config, subscribe to /gs/assignments (keeping only my own), and
broadcast InterceptorState at the configured rate (5 Hz) on
/interceptors/{id}/state.

Guidance (A2), the comms wrapper (A3), awareness (A4) and claim-and-confirm
(A5) build on this. rclpy is imported lazily so the pure logic in
local_state.py stays unit-testable without a ROS2 install.

Run: INTERCEPTOR_ID=i1 python3 -m agent.interceptor_agent
"""

from __future__ import annotations

import os

import rclpy
from contracts.config import ScenarioConfig
from contracts.messages import Assignment, InterceptorState
from contracts.topics import Topics
from rclpy.node import Node

from agent.local_state import InterceptorLocalState

DEFAULT_CONFIG_PATH = "config/scenario_default.yaml"


class InterceptorAgent(Node):  # type: ignore[misc]  # rclpy.Node is untyped
    def __init__(self) -> None:
        interceptor_id = os.environ.get("INTERCEPTOR_ID")
        if not interceptor_id:
            raise RuntimeError("INTERCEPTOR_ID env var must be set (e.g. i1)")

        super().__init__(f"interceptor_agent_{interceptor_id}")

        config_path = os.environ.get("SCENARIO_CONFIG", DEFAULT_CONFIG_PATH)
        self.config = ScenarioConfig.from_yaml(config_path)

        self.state = InterceptorLocalState(
            interceptor_id=interceptor_id,
            launch_position=self.config.interceptors.launch_position,
        )

        # Wall-clock-free time base: seconds since node start.
        self._t0 = self._now()

        # Assignments from the Ground Station (one shared topic for all agents).
        self.create_subscription(
            Assignment,
            Topics.GS_ASSIGNMENTS,
            self._on_assignment,
            10,
        )

        # 5 Hz state broadcast.
        self._state_pub = self.create_publisher(
            InterceptorState,
            Topics.interceptor_state(self.state.id),
            10,
        )
        period_s = 1.0 / self.config.comms.publish_rate_hz
        self.create_timer(period_s, self._publish_state)

        self.get_logger().info(
            f"{self.state.id} up: launch={self.state.position}, "
            f"state @ {self.config.comms.publish_rate_hz} Hz on "
            f"{Topics.interceptor_state(self.state.id)}"
        )

    # -- time --------------------------------------------------------------
    def _now(self) -> float:
        return float(self.get_clock().now().nanoseconds) * 1e-9

    def _elapsed(self) -> float:
        return self._now() - self._t0

    # -- callbacks ---------------------------------------------------------
    def _on_assignment(self, msg: Assignment) -> None:
        # The topic carries one Assignment per interceptor; ignore others'.
        if self.state.apply_assignments([msg]):
            self.get_logger().info(
                f"{self.state.id} assigned -> track {self.state.assigned_track_id}, "
                f"initial_waypoint {self.state.initial_waypoint}"
            )

    def _publish_state(self) -> None:
        self._state_pub.publish(self.state.to_state_msg(self._elapsed()))


def main() -> None:
    rclpy.init()
    node = InterceptorAgent()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
