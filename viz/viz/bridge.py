#!/usr/bin/env python3
"""
viz.bridge — the missing wire between the ROS2 backend and the web dashboard.

`viz/dashboard/src/real_api.ts` polls a small REST API at ``/api/*``; nothing
served it until now. This process subscribes to the live ROS2 topics emitted by
``sim/`` + ``gs/`` + ``agent/``, keeps the latest snapshot in memory, and serves
it over HTTP — so the standalone dashboard runs on real backend data instead of
its in-browser mock.

Endpoints (must match real_api.ts exactly):
    GET  /api/tracks                  -> Track[]
    GET  /api/threats                 -> {track_id, severity, eta_seconds, timestamp}[]
    GET  /api/assignments             -> {interceptor_id, track_id}[]  (live, reflects re-tasking)
    GET  /api/engagement-events       -> EngagementEvent[]
    GET  /api/interceptors/{id}/state -> Interceptor | null
    POST /api/sim/{start,stop,reset}  -> {ok, reason?}

Subscribed topics (JSON-over-std_msgs/String, same envelope the agents use):
    /gs/tracks, /gs/threats, /gs/assignments         (Ground Station / sim stand-in)
    /simulation/ground_truth, /simulation/engagement (sim driver)
    /interceptors/{i1..iN}/state                     (agents, 5 Hz)

The Run / Reset buttons drive the sim backend itself: when ``VIZ_SPAWN_SIM=1``
(default) the bridge launches ``sim.driver`` + one ``agent.interceptor_agent``
per interceptor as subprocesses, so the dashboard is a one-button demo. Set
``VIZ_SPAWN_SIM=0`` under docker compose, where those nodes already run.

Run:  python3 -m viz.bridge        # serves on :8000 (Vite proxies /api here)
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from contracts.config import ScenarioConfig
from contracts.topics import Topics

logging.basicConfig(level=logging.INFO, format="%(asctime)s [bridge] %(message)s")
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = os.environ.get("VIZ_CONFIG", str(REPO_ROOT / "config" / "scenario_default.yaml"))
HTTP_PORT = int(os.environ.get("VIZ_API_PORT", "8000"))
SPAWN_SIM = os.environ.get("VIZ_SPAWN_SIM", "1") not in ("0", "false", "")
MAX_EVENTS = 200  # ring-buffer of engagement events kept for the event log

Vec3 = tuple[float, float, float]


# ── In-memory snapshot of the bus (thread-safe; ROS thread writes, HTTP reads) ──


class Snapshot:
    """Latest message per topic. One lock guards every field — writes are tiny."""

    def __init__(self, target: Vec3) -> None:
        self._lock = threading.Lock()
        self._target = target
        self.tracks: list[dict[str, Any]] = []
        self.threats: list[dict[str, Any]] = []
        self.assignments: list[dict[str, Any]] = []  # latched /gs launch plan
        self.events: list[dict[str, Any]] = []
        self.ground_truth: dict[str, dict[str, Any]] = {}  # object_id -> GroundTruthObject
        self.interceptors: dict[str, dict[str, Any]] = {}  # id -> InterceptorState

    # -- writers (called from the ROS spin thread) --------------------------

    def set_tracks(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            self.tracks = items

    def set_threats(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            self.threats = items

    def set_assignments(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            self.assignments = items

    def add_event(self, ev: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(ev)
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[-MAX_EVENTS:]

    def set_ground_truth(self, objs: list[dict[str, Any]]) -> None:
        with self._lock:
            self.ground_truth = {o["object_id"]: o for o in objs}

    def set_interceptor(self, state: dict[str, Any]) -> None:
        with self._lock:
            self.interceptors[state["interceptor_id"]] = state

    def clear(self) -> None:
        with self._lock:
            self.tracks = []
            self.threats = []
            self.assignments = []
            self.events = []
            self.ground_truth = {}
            self.interceptors = {}

    # -- readers (called from HTTP handlers), shaped to the frontend contract -

    def view_tracks(self) -> list[dict[str, Any]]:
        with self._lock:
            # Prefer fused /gs/tracks; fall back to ground-truth shaheds so the map
            # is never blank even when the real GS isn't running.
            if self.tracks:
                return [
                    {
                        "track_id": t["track_id"],
                        "position": t["position"],
                        "velocity": t["velocity"],
                        "alive": t.get("alive", True),
                        "timestamp": t.get("timestamp", 0.0),
                    }
                    for t in self.tracks
                ]
            return [
                {
                    "track_id": o["object_id"],
                    "position": o["position"],
                    "velocity": o["velocity"],
                    "alive": o.get("alive", True),
                    "timestamp": 0.0,
                }
                for o in self.ground_truth.values()
                if o.get("kind") == "shahed"
            ]

    def view_threats(self) -> list[dict[str, Any]]:
        with self._lock:
            if self.threats:
                return [
                    {
                        "track_id": th["track_id"],
                        "severity": th.get("threat_score", 0.0),
                        "eta_seconds": th.get("eta_seconds", 0.0),
                        "timestamp": th.get("timestamp", 0.0),
                    }
                    for th in self.threats
                ]
            # No real threat scoring on the bus: derive a simple proximity/ETA
            # ranking from live tracks so the threat queue still populates.
            out: list[dict[str, Any]] = []
            for o in self.ground_truth.values():
                if o.get("kind") != "shahed" or not o.get("alive", True):
                    continue
                eta = self._eta(o["position"], o["velocity"])
                out.append(
                    {
                        "track_id": o["object_id"],
                        "severity": 1.0 / (eta + 1.0),
                        "eta_seconds": eta,
                        "timestamp": 0.0,
                    }
                )
            return out

    def view_assignments(self) -> list[dict[str, Any]]:
        with self._lock:
            # Live picture: start from the latched launch plan, then let each
            # interceptor's current target win (this is what re-tasking changes).
            merged: dict[str, str | None] = {
                a["interceptor_id"]: a.get("track_id") for a in self.assignments
            }
            for iid, st in self.interceptors.items():
                merged[iid] = st.get("assigned_track_id")
            return [{"interceptor_id": iid, "track_id": tid} for iid, tid in merged.items()]

    def view_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "timestamp": e.get("timestamp", 0.0),
                    "interceptor_id": e["interceptor_id"],
                    "track_id": e["track_id"],
                    "success": e.get("success", True),
                }
                for e in self.events
            ]

    def view_interceptor(self, iid: str) -> dict[str, Any] | None:
        with self._lock:
            st = self.interceptors.get(iid)
            gt = self.ground_truth.get(iid)
            if st is None and gt is None:
                return None
            position = (st or gt or {}).get("position", [0.0, 0.0, 0.0])
            target = st.get("assigned_track_id") if st else None
            alive = (st or gt or {}).get("alive", True)
            if not alive:
                status = "DESTROYED"
            elif target:
                status = "ENGAGING"
            else:
                status = "READY"
            return {
                "interceptor_id": iid,
                "position": position,
                "target_track_id": target,
                "alive": alive,
                "status": status,
            }

    def _eta(self, pos: Any, vel: Any) -> float:
        d = math.dist(pos[:2], self._target[:2])
        speed = math.hypot(vel[0], vel[1])
        return d / speed if speed > 1e-3 else 999.0


# ── Subprocess lifecycle: Run / Reset actually drive the sim backend ────────────


class SimProcesses:
    """Launches and kills sim.driver + one agent per interceptor."""

    def __init__(self, interceptor_count: int) -> None:
        self._count = interceptor_count
        self._procs: list[subprocess.Popen[bytes]] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self._procs) and any(p.poll() is None for p in self._procs)

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self.running:
                return True, "already running"
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            specs: list[list[str]] = [[sys.executable, "-m", "sim.driver"]]
            self._procs = []
            try:
                self._procs.append(
                    subprocess.Popen(specs[0], cwd=str(REPO_ROOT), env=env)  # noqa: S603
                )
                for n in range(1, self._count + 1):
                    agent_env = {**env, "INTERCEPTOR_ID": f"i{n}"}
                    self._procs.append(
                        subprocess.Popen(  # noqa: S603
                            [sys.executable, "-m", "agent.interceptor_agent"],
                            cwd=str(REPO_ROOT),
                            env=agent_env,
                        )
                    )
            except OSError as exc:
                self._kill_locked()
                return False, f"spawn failed: {exc}"
            log.info("launched sim backend: driver + %d agents", self._count)
            return True, "started"

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            self._kill_locked()
            return True, "stopped"

    def _kill_locked(self) -> None:
        for p in self._procs:
            if p.poll() is None:
                p.terminate()
        for p in self._procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        self._procs = []


# ── ROS2 node: decode the bus into the snapshot ─────────────────────────────────


def _start_ros(snap: Snapshot, interceptor_count: int) -> None:
    """Spin a subscriber node on a daemon thread. Lazy rclpy import (headless-safe)."""
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import (
        QoSDurabilityPolicy,
        QoSHistoryPolicy,
        QoSProfile,
        QoSReliabilityPolicy,
    )
    from std_msgs.msg import String

    # Match the driver's latched assignments publisher, or DDS drops the one-shot.
    latched = QoSProfile(
        depth=1,
        history=QoSHistoryPolicy.KEEP_LAST,
        reliability=QoSReliabilityPolicy.RELIABLE,
        durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    )

    class BridgeNode(Node):  # type: ignore[misc]  # rclpy.Node is untyped
        def __init__(self) -> None:
            super().__init__("viz_bridge")
            self.create_subscription(String, Topics.GS_TRACKS, self._on_tracks, 10)
            self.create_subscription(String, Topics.GS_THREATS, self._on_threats, 10)
            self.create_subscription(String, Topics.GS_ASSIGNMENTS, self._on_assignments, latched)
            self.create_subscription(String, Topics.GROUND_TRUTH, self._on_ground_truth, 10)
            self.create_subscription(String, Topics.ENGAGEMENT, self._on_engagement, 10)
            for n in range(1, interceptor_count + 1):
                topic = Topics.interceptor_state(f"i{n}")
                self.create_subscription(String, topic, self._on_interceptor, 10)
            self.get_logger().info(f"viz_bridge subscribed ({interceptor_count} interceptors)")

        def _on_tracks(self, msg: Any) -> None:
            snap.set_tracks(json.loads(msg.data))

        def _on_threats(self, msg: Any) -> None:
            snap.set_threats(json.loads(msg.data))

        def _on_assignments(self, msg: Any) -> None:
            snap.set_assignments(json.loads(msg.data))

        def _on_ground_truth(self, msg: Any) -> None:
            snap.set_ground_truth(json.loads(msg.data))

        def _on_engagement(self, msg: Any) -> None:
            snap.add_event(json.loads(msg.data))

        def _on_interceptor(self, msg: Any) -> None:
            snap.set_interceptor(json.loads(msg.data))

    rclpy.init()
    node = BridgeNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


# ── FastAPI app ─────────────────────────────────────────────────────────────────


def _load_config() -> ScenarioConfig:
    return ScenarioConfig.from_yaml(CONFIG_PATH)


_cfg = _load_config()
_snap = Snapshot(tuple(_cfg.scenario.target_position))
_sim = SimProcesses(_cfg.interceptors.count)


def create_app() -> Any:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="viz-bridge", version="0.1.0")
    # The dashboard may be served from a different origin (Vite :5173) than the
    # API (:8000); the dev proxy avoids CORS, but allow it for direct access too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/tracks")
    def get_tracks() -> list[dict[str, Any]]:
        return _snap.view_tracks()

    @app.get("/api/threats")
    def get_threats() -> list[dict[str, Any]]:
        return _snap.view_threats()

    @app.get("/api/assignments")
    def get_assignments() -> list[dict[str, Any]]:
        return _snap.view_assignments()

    @app.get("/api/engagement-events")
    def get_events() -> list[dict[str, Any]]:
        return _snap.view_events()

    @app.get("/api/interceptors/{iid}/state")
    def get_interceptor(iid: str) -> dict[str, Any] | None:
        return _snap.view_interceptor(iid)

    @app.post("/api/sim/start")
    def sim_start() -> dict[str, Any]:
        if not SPAWN_SIM:
            return {"ok": True, "reason": "sim managed externally (docker compose)"}
        ok, reason = _sim.start()
        return {"ok": ok, "reason": reason}

    @app.post("/api/sim/stop")
    def sim_stop() -> dict[str, Any]:
        if not SPAWN_SIM:
            return {"ok": True, "reason": "sim managed externally (docker compose)"}
        ok, reason = _sim.stop()
        return {"ok": ok, "reason": reason}

    @app.post("/api/sim/reset")
    def sim_reset() -> dict[str, Any]:
        _snap.clear()
        if not SPAWN_SIM:
            return {"ok": True, "reason": "snapshot cleared; sim managed externally"}
        _sim.stop()
        ok, reason = _sim.start()
        return {"ok": ok, "reason": reason}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "sim_running": _sim.running, "spawn_sim": SPAWN_SIM}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    ros_thread = threading.Thread(
        target=_start_ros, args=(_snap, _cfg.interceptors.count), daemon=True
    )
    ros_thread.start()
    log.info("viz-bridge serving on http://0.0.0.0:%d (spawn_sim=%s)", HTTP_PORT, SPAWN_SIM)
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="warning")


if __name__ == "__main__":
    main()
