"""
Pure, ROS2-free local state for a single interceptor.

Keeping this free of rclpy lets the agent's logic be unit-tested headless
(the comms layer is isolated in A3). A1 only needs: hold my own kinematic
state, absorb my assignment, and emit an InterceptorState snapshot.
"""

from __future__ import annotations

from contracts.messages import Assignment, InterceptorState

Vec3 = tuple[float, float, float]


def select_assignment(assignments: list[Assignment], my_id: str) -> Assignment | None:
    """Pick this interceptor's assignment, ignoring every other agent's.

    The GS publishes one Assignment per interceptor on a shared topic; each
    agent must keep only the message whose interceptor_id matches its own. If
    several match (duplicate/re-issued), the last one wins.
    """
    mine = [a for a in assignments if a.interceptor_id == my_id]
    return mine[-1] if mine else None


class InterceptorLocalState:
    """Authoritative local view of *this* interceptor.

    In A1 position/velocity are simulated internally (seeded at the launch
    position). From A2 onward the real pose is driven by the sim loop, but the
    publish path stays identical.
    """

    def __init__(self, interceptor_id: str, launch_position: Vec3):
        self.id = interceptor_id
        self.position: Vec3 = launch_position
        self.velocity: Vec3 = (0.0, 0.0, 0.0)
        self.assigned_track_id: str | None = None
        self.initial_waypoint: Vec3 | None = None
        self.alive: bool = True

    def apply_assignments(self, assignments: list[Assignment]) -> bool:
        """Absorb a batch from /gs/assignments. Returns True if mine changed."""
        mine = select_assignment(assignments, self.id)
        if mine is None:
            return False
        changed = (
            mine.track_id != self.assigned_track_id
            or mine.initial_waypoint != self.initial_waypoint
        )
        self.assigned_track_id = mine.track_id
        self.initial_waypoint = mine.initial_waypoint
        return changed

    def to_state_msg(self, timestamp: float) -> InterceptorState:
        """Snapshot for the 5 Hz broadcast on /interceptors/{id}/state."""
        return InterceptorState(
            interceptor_id=self.id,
            position=self.position,
            velocity=self.velocity,
            assigned_track_id=self.assigned_track_id,
            alive=self.alive,
            timestamp=timestamp,
        )
