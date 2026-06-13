"""
Interceptor agent node — Team 3.

A1 skeleton: start a ROS2 node, read INTERCEPTOR_ID from the env, load the
scenario config, subscribe to /gs/assignments (keeping only my own), and
broadcast InterceptorState at the configured rate (5 Hz) on
/interceptors/{id}/state.

Two integration traps handled here (see also serde.py):
  - Serialization: contracts are dataclasses, not ROS msgs, so every message
    travels as JSON inside std_msgs/String via `serde.encode/decode`.
  - Assignment temporality: the GS publishes the assignment ONCE at launch;
    if this node starts late it would miss a VOLATILE message. The assignment
    subscriber therefore uses TRANSIENT_LOCAL durability so a latched message
    is delivered on join. The GS publisher MUST use TRANSIENT_LOCAL too, or
    DDS QoS-incompatibility silently prevents any delivery.

A2 adds PN guidance: subscribe /gs/tracks (passive fusion continues post-launch,
Q3), and publish a WaypointCommand at guidance.update_rate_hz (10 Hz) on
/interceptors/{id}/waypoint. The waypoint is the PN "carrot" (see guidance.py),
falling back to the initial_waypoint when no live track is available.

The comms wrapper (A3), awareness (A4) and CBAA re-tasking (A5) build on this.
A5 collapses the old claim/commit pair into the single InterceptorState
broadcast: the decision tick decides ownership AND emits, so one timer at the
decision period (5 Hz) drives both. The pure logic in local_state.py /
serde.py / guidance.py / retasking.py stays unit-testable without a ROS2 install.

Run: INTERCEPTOR_ID=i1 python3 -m agent.interceptor_agent
"""

from __future__ import annotations

import os

import rclpy
from contracts.config import ScenarioConfig
from contracts.messages import (
    Assignment,
    EngagementEvent,
    GroundTruthObject,
    InterceptorState,
    Track,
    WaypointCommand,
)
from contracts.topics import Topics
from rclpy.node import Node

from agent import guidance
from agent.awareness import AwarenessPicture
from agent.comms import LATCHED_QOS, Comms
from agent.local_state import InterceptorLocalState
from agent.packet_loss import PacketDropper, agent_seed
from agent.retasking import RetaskingProtocol

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

        # All messaging goes through Comms (the single ROS seam). Packet loss is
        # seeded per-agent for reproducible-yet-uncorrelated drops (FR-7.2).
        dropper = PacketDropper(
            self.config.comms.packet_loss_prob,
            agent_seed(self.config.scenario.seed, self.state.id),
        )
        self.comms = Comms(self, dropper)

        # Local awareness picture, built from peer broadcasts (FR-7.3). The
        # protected point weights the CBAA priority key (danger tie-break).
        self.picture = AwarenessPicture(
            self.state.id,
            self.config.comms.staleness_timeout_s,
            protected_point=self.config.scenario.target_position,
        )

        # CBAA decentralised re-tasking (A5, FR-8). Pure engine; the node owns
        # the Comms seam and feeds it peer state (via the picture) plus a
        # periodic tick. Its only output is the InterceptorState broadcast.
        rt = self.config.retasking
        self.retask = RetaskingProtocol(
            self.state,
            self.picture,
            emit_state=self._publish_state_msg,
            range_m=self.config.interceptors.range_m,
            speed_mps=self.config.interceptors.speed_mps,
            protected_point=self.config.scenario.target_position,
            lock_threshold_s=rt.lock_threshold_s,
            bucket_size_s=rt.bucket_size_s,
            bucket_hysteresis_s=rt.bucket_hysteresis_s,
            incumbency_margin=rt.incumbency_margin,
            change_repeat=rt.change_repeat,
            heartbeat_period_s=rt.heartbeat_period_s,
            silence_timeout_s=rt.silence_timeout_s,
        )

        # GS topics are not lossy: assignments are latched (one-shot at launch),
        # tracks are passive fusion. Peer topics ARE lossy (FR-7.2).
        self.comms.subscribe_list(
            Topics.GS_ASSIGNMENTS, Assignment, self._on_assignments, qos=LATCHED_QOS
        )
        self.comms.subscribe_list(Topics.GS_TRACKS, Track, self._on_tracks)
        self.comms.subscribe(Topics.ENGAGEMENT, EngagementEvent, self.picture.on_engagement)

        # Sim → agent pose feedback: the sim owns kinematics, so we copy our own
        # object out of the ground-truth stream into local_state. Without this the
        # PN guidance and the 5 Hz peer broadcast run on the frozen launch seed.
        # (The GroundTruth wrapper is flattened to its objects[] on the wire —
        # serde does not recurse into a dataclass's nested dataclass fields.)
        self.comms.subscribe_list(Topics.GROUND_TRUTH, GroundTruthObject, self._on_ground_truth)

        # Peer state broadcasts (all other interceptors) — the only peer channel
        # in CBAA. Convention: ids i1..iN. Peer channels are lossy (FR-7.2).
        for peer_id in self._peer_ids():
            self.comms.subscribe(
                Topics.interceptor_state(peer_id),
                InterceptorState,
                self.picture.on_peer_state,
                lossy=True,
            )

        # State broadcast: the CBAA tick decides AND emits, so a single timer at
        # the decision period (5 Hz) drives both. emit_state publishes here.
        self._state_topic = Topics.interceptor_state(self.state.id)
        self.comms.advertise(self._state_topic)

        # 10 Hz waypoint command to the simulation.
        self._waypoint_topic = Topics.waypoint_command(self.state.id)
        self.comms.advertise(self._waypoint_topic)
        self.create_timer(1.0 / self.config.guidance.update_rate_hz, self._publish_waypoint)

        # 2 Hz awareness check (A4 conflict log).
        self._conflict_logged = False
        self._last_logged_owns: str | None = None
        self.create_timer(0.5, self._check_awareness)

        # Drive the CBAA engine (A5) at the decision period. Each tick rebuilds
        # the decision from the latest picture and broadcasts our state.
        self.create_timer(self.config.retasking.decision_period_s, self._retask_tick)

        self.get_logger().info(
            f"{self.state.id} up: launch={self.state.position}, "
            f"CBAA @ {1.0 / self.config.retasking.decision_period_s:.0f} Hz, "
            f"waypoints @ {self.config.guidance.update_rate_hz} Hz"
        )

    # -- helpers -----------------------------------------------------------
    def _now(self) -> float:
        return float(self.get_clock().now().nanoseconds) * 1e-9

    def _elapsed(self) -> float:
        return self._now() - self._t0

    def _peer_ids(self) -> list[str]:
        # Convention (flagged for the team): interceptor ids are i1..iN.
        count = self.config.interceptors.count
        return [f"i{n}" for n in range(1, count + 1) if f"i{n}" != self.state.id]

    # -- callbacks (Comms hands these already-decoded) ---------------------
    def _on_assignments(self, assignments: list[Assignment]) -> None:
        # Assignment[] batch; apply_assignments keeps only ours, ignores others'.
        if self.state.apply_assignments(assignments):
            self.get_logger().info(
                f"{self.state.id} assigned -> track {self.state.assigned_track_id}, "
                f"initial_waypoint {self.state.initial_waypoint}"
            )

    def _on_tracks(self, tracks: list[Track]) -> None:
        self.state.update_tracks(tracks)
        self.picture.on_tracks(tracks)

    def _on_ground_truth(self, objects: list[GroundTruthObject]) -> None:
        # The sim drives our real pose; adopt it so guidance + the state broadcast
        # are not stuck at the launch seed. We only care about our own object.
        me = next((o for o in objects if o.object_id == self.state.id), None)
        if me is None:
            return
        self.state.position = me.position
        self.state.velocity = me.velocity
        self.state.alive = me.alive

    def _publish_state_msg(self, msg: InterceptorState) -> None:
        # CBAA emit_state seam: the engine built the message (ownership, key,
        # lock, seq); we just put it on the wire and log ownership changes.
        if msg.assigned_track_id != self._last_logged_owns:
            self.get_logger().info(
                f"{self.state.id} owns -> {msg.assigned_track_id}"
                f"{' [LOCKED]' if msg.locked else ''}"
            )
            self._last_logged_owns = msg.assigned_track_id
        self.comms.publish(self._state_topic, msg)

    def _check_awareness(self) -> None:
        conflict = self.picture.has_coverage_conflict()
        if conflict and not self._conflict_logged:
            self.get_logger().info(
                f"{self.state.id} coverage conflict: uncovered="
                f"{sorted(self.picture.uncovered_active_tracks())}"
            )
        self._conflict_logged = conflict

    def _retask_tick(self) -> None:
        self.retask.tick(self._elapsed())

    def _publish_waypoint(self) -> None:
        target = self.state.target()
        if target is not None and target.alive:
            point = guidance.steering_waypoint(
                self.state.position,
                self.state.velocity,
                target.position,
                target.velocity,
                nav_constant=self.config.guidance.nav_constant,
                speed_mps=self.config.interceptors.speed_mps,
                max_turn_rate_deg_s=self.config.interceptors.max_turn_rate_deg_s,
                dt=1.0 / self.config.guidance.update_rate_hz,
                lookahead_s=self.config.guidance.lookahead_s,
            )
        else:
            # No live track yet (or it's dead): hold the pre-launch waypoint.
            point = self.state.initial_waypoint or self.state.position
        cmd = WaypointCommand(self.state.id, point, self._elapsed())
        self.comms.publish(self._waypoint_topic, cmd)


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
