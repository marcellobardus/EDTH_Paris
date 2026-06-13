#!/usr/bin/env python3
"""
LIVE track viewer — the visual end of the distributed pipeline.

Subscribes to the ground station's fused ``Track`` stream on ``/gs/tracks`` over
ZeroMQ and draws each confirmed Shahed on a real-time 2-D map: a stable colour +
"Shahed A/B/C..." label + a motion trail, closing on the defended target at the
origin. This is the 4th process in the chain:

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

import matplotlib.pyplot as plt

from contracts.messages import Track
from contracts.topics import Topics

PALETTE = list(plt.cm.tab10.colors)
LETTERS = string.ascii_uppercase


class TrackViewer:
    """Accumulates the /gs/tracks stream and renders the live threat picture."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.tracks: dict[str, Track] = {}          # track_id -> latest Track
        self.last_seen: dict[str, float] = {}        # track_id -> latest scenario time
        self.labels: OrderedDict[str, tuple[str, tuple]] = OrderedDict()
        self.trails: dict[str, deque] = {}
        self.clock = 0.0                             # latest scenario timestamp seen

    # -- ingest (called by the bus handler, one Track at a time) -------------

    def on_track(self, track: Track) -> None:
        tid = track.track_id
        self.tracks[tid] = track
        self.last_seen[tid] = track.timestamp
        self.clock = max(self.clock, track.timestamp)
        if tid not in self.labels:                   # newly seen → name + colour
            idx = len(self.labels)
            self.labels[tid] = (
                f"Shahed {LETTERS[idx % len(LETTERS)]}",
                PALETTE[idx % len(PALETTE)],
            )
            self.trails[tid] = deque(maxlen=60)
        self.trails[tid].append((track.position[0], track.position[1]))

    def expire(self) -> None:
        """Drop tracks the GS has stopped publishing (dropped/coasted-out)."""
        for tid in list(self.tracks):
            if self.clock - self.last_seen[tid] > self.args.expiry:
                del self.tracks[tid]
                del self.last_seen[tid]
                self.trails.pop(tid, None)

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
            ax.add_patch(plt.Circle((0, 0), r, fill=False, ls="--",
                                    color="#33405e", alpha=0.7))

        for tid, track in self.tracks.items():
            name, colour = self.labels[tid]
            trail = self.trails[tid]
            if len(trail) > 1:
                tx, ty = zip(*trail)
                ax.plot(tx, ty, color=colour, lw=1.6, alpha=0.8, zorder=4)
            x, y = track.position[0], track.position[1]
            ax.plot(x, y, marker="o", color=colour, ms=9, zorder=5,
                    markeredgecolor="white", markeredgewidth=0.6)
            ax.annotate(f"{name}  ({math.hypot(x, y):.0f} m)", (x, y),
                        textcoords="offset points", xytext=(9, 5),
                        color=colour, fontsize=9, fontweight="bold")

        ax.set_title(
            f"/gs/tracks (live)   sim t = {self.clock:>5.1f} s   "
            f"classified threats: {len(self.tracks)}",
            color="white", fontsize=11,
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tracks-addr", default="tcp://127.0.0.1:5557",
                   help="ground station's GS_TRACKS PUB address (we connect)")
    p.add_argument("--interval", type=int, default=200, help="ms per redraw")
    p.add_argument("--expiry", type=float, default=3.0,
                   help="drop a track after this many sim-seconds without an update")
    p.add_argument("--limit", type=int, default=4500, help="map half-width (m)")
    p.add_argument("--demo", action="store_true",
                   help="headless: inject synthetic tracks → PNG (no ZMQ, no window)")
    p.add_argument("--out", default="viz/track_viewer.png")
    args = p.parse_args()

    viewer = TrackViewer(args)

    if args.demo:  # verify render + labelling without the live pipeline
        for k in range(1, 26):
            for i, (x0, y0, vx, vy) in enumerate(
                [(-3000, 0, 50, 0), (0, -3000, 0, 50), (2500, 500, -45, 0)]
            ):
                viewer.on_track(Track(
                    track_id=f"synthetic-{i}",
                    position=(x0 + vx * k, y0 + vy * k, 100.0),
                    velocity=(vx, vy, 0.0),
                    covariance=[[0.0] * 6 for _ in range(6)],
                    alive=True, timestamp=float(k),
                ))
        fig, ax = plt.subplots(figsize=(9, 9))
        viewer.render(ax)
        fig.savefig(args.out, dpi=120, facecolor="#0b1021")
        print(f"[demo] rendered {len(viewer.tracks)} synthetic tracks -> {args.out}")
        return

    from agent.bus import ZmqBus

    bus = ZmqBus(args.tracks_addr, bind=False)       # GS binds PUB; we connect SUB
    bus.subscribe(Topics.GS_TRACKS, Track, viewer.on_track)

    matplotlib.use("macosx", force=True)
    from matplotlib.animation import FuncAnimation

    fig, ax = plt.subplots(figsize=(9, 9))
    fig.patch.set_facecolor("#0b1021")

    def update(_frame):
        bus.spin(timeout_ms=10)                      # drain any waiting tracks
        viewer.expire()
        viewer.render(ax)

    ani = FuncAnimation(fig, update, interval=args.interval, cache_frame_data=False)
    fig._ani = ani  # noqa: SLF001 — pin so it isn't garbage-collected
    print(f"track viewer connected to {Topics.GS_TRACKS} at {args.tracks_addr}. "
          f"Waiting for the ground station… close the window to stop.")
    plt.show()


if __name__ == "__main__":
    main()
