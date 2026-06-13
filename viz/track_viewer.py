#!/usr/bin/env python3
"""
LIVE track viewer — the visual end of the distributed pipeline.

Subscribes to the ground station's fused ``Track`` stream on ``/gs/tracks`` and
its ``ThreatAssessment`` scores on ``/gs/threats`` over ZeroMQ, and draws each
confirmed Shahed on a real-time 2-D map: a stable colour + "Shahed A/B/C..."
label + a motion trail, closing on the defended target at the origin. Each
target also carries its **threat weight** — a red halo and marker that grow with
the score, plus a numeric ``thr``/``eta`` label — so the most imminent threats
visibly dominate. This is the 4th process in the chain:

    world_node ──► radar_sensor_node ──► gs_node ──► track_viewer (this)
       truth          noisy detections     fused tracks    the picture

Unlike ``live_tracker.py`` (which runs the radar + tracker in-process), this node
runs NOTHING itself — it only renders what the real ground station publishes. So
it shows the genuine distributed flow on screen.

Run it LAST (it connects; the ground station binds the tracks endpoint):

    uv run python viz/track_viewer.py
    uv run python viz/track_viewer.py --tracks-addr tcp://127.0.0.1:5557

Note: only CONFIRMED tracks cross /gs/tracks, so this view shows classified
threats, not the raw pre-confirmation blips (those never leave the GS — surfacing
them downstream would need an XPUB/XSUB proxy, out of scope for the simple bus).
"""

from __future__ import annotations

import argparse
import math
import string
from collections import OrderedDict, deque

import matplotlib

matplotlib.use("Agg")  # default; switched to 'macosx' for the live window in main()

import matplotlib.pyplot as plt  # noqa: I001 — must import after matplotlib.use() above

from contracts.messages import Assignment, ThreatAssessment, Track
from contracts.topics import Topics

PALETTE = list(plt.cm.tab10.colors)
LETTERS = string.ascii_uppercase

# Threat scores are ~1/eta (s): an inbound drone 50 s out scores ~0.02, 10 s out
# ~0.1. Map that range onto marker/halo size and a danger colour so the most
# imminent threats visibly dominate the picture.
THREAT_FULL_SCALE = 0.15  # score at/above which the threat visual is maxed out


class TrackViewer:
    """Accumulates the /gs/tracks stream and renders the live threat picture."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.tracks: dict[str, Track] = {}  # track_id -> latest Track
        self.threats: dict[str, ThreatAssessment] = {}  # track_id -> latest threat
        self.last_seen: dict[str, float] = {}  # track_id -> latest scenario time
        self.labels: OrderedDict[str, tuple[str, tuple]] = OrderedDict()
        self.trails: dict[str, deque] = {}
        # interceptor_id -> {track_id, waypoint, timestamp} (from /gs/assignments)
        self.assignments: dict[str, dict] = {}
        self.clock = 0.0  # latest scenario timestamp seen

    # -- ingest (called by the bus handlers, one message at a time) ----------

    def on_track(self, track: Track) -> None:
        tid = track.track_id
        self.tracks[tid] = track
        self.last_seen[tid] = track.timestamp
        self.clock = max(self.clock, track.timestamp)
        if tid not in self.labels:  # newly seen → name + colour
            idx = len(self.labels)
            self.labels[tid] = (
                f"Shahed {LETTERS[idx % len(LETTERS)]}",
                PALETTE[idx % len(PALETTE)],
            )
            self.trails[tid] = deque(maxlen=60)
        self.trails[tid].append((track.position[0], track.position[1]))

    def on_threat(self, threat: ThreatAssessment) -> None:
        """Latest threat score for a track (from /gs/threats)."""
        self.threats[threat.track_id] = threat
        self.clock = max(self.clock, threat.timestamp)

    def on_assignment(self, assignment: Assignment) -> None:
        """Latest interceptor→track assignment (from /gs/assignments)."""
        self.assignments[assignment.interceptor_id] = {
            "track_id": assignment.track_id,
            "waypoint": assignment.initial_waypoint,
            "timestamp": assignment.timestamp,
        }
        self.clock = max(self.clock, assignment.timestamp)

    def expire(self) -> None:
        """Drop tracks/assignments the GS has stopped publishing."""
        for tid in list(self.tracks):
            if self.clock - self.last_seen[tid] > self.args.expiry:
                del self.tracks[tid]
                del self.last_seen[tid]
                self.trails.pop(tid, None)
                self.threats.pop(tid, None)
        for iid in list(self.assignments):
            if self.clock - self.assignments[iid]["timestamp"] > self.args.expiry:
                del self.assignments[iid]

    def _assigned_to(self) -> dict[str, list[str]]:
        """track_id -> the interceptor ids currently assigned to it."""
        out: dict[str, list[str]] = {}
        for iid, a in self.assignments.items():
            out.setdefault(a["track_id"], []).append(iid)
        return out

    # -- draw ----------------------------------------------------------------

    def render(self, ax) -> None:
        lim = self.args.limit
        ax.clear()
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_aspect("equal")
        ax.grid(True, alpha=0.25)
        ax.set_facecolor("#0b1021")
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")

        ax.plot(0, 0, marker="*", color="red", markersize=20, zorder=6)
        for r in range(1000, lim + 1, 1000):
            ax.add_patch(plt.Circle((0, 0), r, fill=False, ls="--", color="#33405e", alpha=0.7))

        assigned = self._assigned_to()

        # Draw least-dangerous first so the highest-threat markers/labels sit on top.
        for tid in sorted(self.tracks, key=self._threat_score):
            track = self.tracks[tid]
            name, colour = self.labels[tid]
            score = self._threat_score(tid)
            urgency = min(score / THREAT_FULL_SCALE, 1.0)  # 0..1 for visuals

            trail = self.trails[tid]
            if len(trail) > 1:
                tx, ty = zip(*trail)
                ax.plot(tx, ty, color=colour, lw=1.6, alpha=0.8, zorder=4)
            x, y = track.position[0], track.position[1]

            # Assignment: dotted line to each assigned interceptor's intercept
            # point, marked with an ×, so you see who is tasked to this target.
            for iid in assigned.get(tid, []):
                wx, wy = self.assignments[iid]["waypoint"][:2]
                ax.plot([x, wx], [y, wy], color=colour, ls=":", lw=1.1, alpha=0.75, zorder=3)
                ax.plot(wx, wy, marker="x", color=colour, ms=7, mew=1.6, zorder=4)

            # Threat "weight": a red halo + marker that grow with urgency.
            ax.scatter(
                x,
                y,
                s=120 + 900 * urgency,
                color="red",
                alpha=0.10 + 0.35 * urgency,
                edgecolors="none",
                zorder=4,
            )
            ax.plot(
                x,
                y,
                marker="o",
                color=colour,
                ms=9 + 8 * urgency,
                zorder=5,
                markeredgecolor="white",
                markeredgewidth=0.6,
            )

            eta = self.threats[tid].eta_seconds if tid in self.threats else None
            eta_str = "∞" if eta is None or eta >= 1e8 else f"{eta:.0f}s"
            tag = "  ◀ " + ",".join(sorted(assigned[tid])) if tid in assigned else ""
            ax.annotate(
                f"{name}  thr {score:.3f}  (eta {eta_str}, {math.hypot(x, y):.0f} m){tag}",
                (x, y),
                textcoords="offset points",
                xytext=(11, 6),
                color=colour,
                fontsize=9,
                fontweight="bold",
            )

        # Assignment roster — which interceptor is tasked to which threat.
        if self.assignments:
            rows = []
            for iid in sorted(self.assignments):
                tid = self.assignments[iid]["track_id"]
                tgt = self.labels[tid][0] if tid in self.labels else tid[:6]
                rows.append(f"{iid} → {tgt}")
            ax.text(
                0.015,
                0.985,
                "ASSIGNMENTS\n" + "\n".join(rows),
                transform=ax.transAxes,
                va="top",
                ha="left",
                color="white",
                fontsize=9,
                family="monospace",
                bbox={"boxstyle": "round", "fc": "#16203a", "ec": "#33405e", "alpha": 0.9},
                zorder=7,
            )

        top = max(self.tracks, key=self._threat_score, default=None)
        top_str = (
            f"  top: {self.labels[top][0]} ({self._threat_score(top):.3f})"
            if top is not None and self.tracks
            else ""
        )
        ax.set_title(
            f"/gs/tracks + /gs/threats + /gs/assignments (live)   sim t = {self.clock:>5.1f} s   "
            f"threats: {len(self.tracks)}  assigned: {len(self.assignments)}{top_str}",
            color="white",
            fontsize=11,
        )

    def _threat_score(self, tid: str) -> float:
        threat = self.threats.get(tid)
        return threat.threat_score if threat is not None else 0.0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--tracks-addr",
        default="tcp://127.0.0.1:5557",
        help="ground station's GS_TRACKS PUB address (we connect)",
    )
    p.add_argument(
        "--assignments-addr",
        default="tcp://127.0.0.1:5558",
        help="assignment node's GS_ASSIGNMENTS PUB address (we connect)",
    )
    p.add_argument("--interval", type=int, default=200, help="ms per redraw")
    p.add_argument(
        "--expiry",
        type=float,
        default=3.0,
        help="drop a track after this many sim-seconds without an update",
    )
    p.add_argument("--limit", type=int, default=4500, help="map half-width (m)")
    p.add_argument(
        "--demo",
        action="store_true",
        help="headless: inject synthetic tracks → PNG (no ZMQ, no window)",
    )
    p.add_argument("--out", default="viz/track_viewer.png")
    args = p.parse_args()

    viewer = TrackViewer(args)

    if args.demo:  # verify render + labelling without the live pipeline
        # Three drones at deliberately different threat levels: a close, fast
        # one (high weight), a mid one, and a far, glancing one (low weight).
        for k in range(1, 19):
            for i, (x0, y0, vx, vy) in enumerate(
                [(-1500, 0, 70, 0), (0, -2500, 0, 45), (3000, 2000, -30, -15)]
            ):
                pos = (x0 + vx * k, y0 + vy * k, 100.0)
                viewer.on_track(
                    Track(
                        track_id=f"synthetic-{i}",
                        position=pos,
                        velocity=(vx, vy, 0.0),
                        covariance=[[0.0] * 6 for _ in range(6)],
                        alive=True,
                        timestamp=float(k),
                    )
                )
                # Synthesise a matching threat score (eta to origin) so the demo
                # PNG exercises the weight visuals without a live ground station.
                dist = math.hypot(pos[0], pos[1])
                closing = -(pos[0] * vx + pos[1] * vy) / dist if dist else 0.0
                eta = dist / closing if closing > 1.0 else 1e9
                viewer.on_threat(
                    ThreatAssessment(
                        track_id=f"synthetic-{i}",
                        position=pos,
                        velocity=(vx, vy, 0.0),
                        threat_score=1.0 / max(eta, 1e-3),
                        eta_seconds=eta,
                        timestamp=float(k),
                    )
                )
        # Synthetic assignments so the demo PNG exercises the roster + lines.
        for i, iid in enumerate(("i1", "i2", "i3")):
            tid = f"synthetic-{i}"
            tp = viewer.tracks[tid].position
            viewer.on_assignment(Assignment(iid, tid, (tp[0] * 0.6, tp[1] * 0.6, tp[2]), 18.0))
        fig, ax = plt.subplots(figsize=(9, 9))
        viewer.render(ax)
        fig.savefig(args.out, dpi=120, facecolor="#0b1021")
        print(
            f"[demo] rendered {len(viewer.tracks)} tracks, "
            f"{len(viewer.assignments)} assignments -> {args.out}"
        )
        return

    from agent.bus import ZmqBus

    bus = ZmqBus(args.tracks_addr, bind=False)  # GS binds PUB; we connect SUB
    bus.subscribe(Topics.GS_TRACKS, Track, viewer.on_track)
    bus.subscribe(Topics.GS_THREATS, ThreatAssessment, viewer.on_threat)
    assign_bus = ZmqBus(args.assignments_addr, bind=False)  # assignment node binds PUB
    assign_bus.subscribe(Topics.GS_ASSIGNMENTS, Assignment, viewer.on_assignment)

    matplotlib.use("macosx", force=True)
    from matplotlib.animation import FuncAnimation

    fig, ax = plt.subplots(figsize=(9, 9))
    fig.patch.set_facecolor("#0b1021")

    def update(_frame):
        bus.spin(timeout_ms=10)  # drain waiting tracks + threats
        assign_bus.spin(timeout_ms=0)  # drain waiting assignments
        viewer.expire()
        viewer.render(ax)

    ani = FuncAnimation(fig, update, interval=args.interval, cache_frame_data=False)
    fig._ani = ani  # noqa: SLF001 — pin so it isn't garbage-collected
    print(
        f"track viewer connected to {Topics.GS_TRACKS} at {args.tracks_addr}. "
        f"Waiting for the ground station… close the window to stop."
    )
    plt.show()


if __name__ == "__main__":
    main()
