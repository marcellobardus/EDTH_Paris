#!/usr/bin/env python3
"""
LIVE ground-station tracking app.

A real-time 2-D map of the airspace, driven tick-by-tick by the real pipeline:

    StoneSoupRadar  ->  MultiTargetTracker

New drones fly in over time. You watch the lifecycle happen live:

  * a raw radar detection shows up as a faint grey **blip** (unidentified);
  * once the tracker confirms it (M-of-N), it is promoted to a named threat —
    **"Shahed A", "Shahed B", ...** — and gets a **stable colour** + a motion
    trail that follows it in toward the defended target at the origin;
  * if it flies out of range and the track is dropped, its label is retired.

Runs in a native macOS window (no Tk needed). Close the window to stop.

    uv run python viz/live_tracker.py
    uv run python viz/live_tracker.py --interval 150 --max-drones 8 --prob-detect 0.85
    uv run python viz/live_tracker.py --headless --frames 60   # smoke test -> PNG, no window
"""

from __future__ import annotations

import argparse
import math
import random
import string
from collections import OrderedDict, deque
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")  # safe default; switched to 'macosx' in main() for the live window

import matplotlib.pyplot as plt

from agent.bus import MockBroker
from gs.tracker import MultiTargetTracker
from sim.radar_stonesoup import StoneSoupRadar, TargetInit

T0 = datetime(2026, 6, 13, 12, 0, 0)
PALETTE = list(plt.cm.tab10.colors)      # 10 distinct, stable threat colours
LETTERS = string.ascii_uppercase


class LiveTracker:
    """Holds the running radar + tracker and the detection→threat display state."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.rng = random.Random(args.seed)
        self.radar = StoneSoupRadar(
            MockBroker().endpoint("radar1"), "radar1", [],
            start_time=T0, seed=args.seed,
            prob_detect=args.prob_detect, position_noise_m=args.position_noise,
            cull_range_m=args.cull,        # drones that fly past + away are removed
        )
        self.tracker = MultiTargetTracker(start_time=T0)

        self.k = 0                          # scan / sim-second counter
        self.spawned = 0
        self.next_spawn = 1                 # spawn the first drone almost immediately
        self.labels: OrderedDict[str, tuple[str, tuple]] = OrderedDict()  # track_id -> (name, colour)
        self.trails: dict[str, deque] = {}  # track_id -> recent (x, y)
        self.cur_dets: list[tuple[float, float]] = []
        self.live: list = []                # confirmed tracks this tick

    # -- simulation ----------------------------------------------------------

    def _spawn_one(self) -> None:
        bearing = self.rng.uniform(0, 2 * math.pi)
        dist = self.rng.uniform(2500.0, 4000.0)
        speed = self.rng.uniform(35.0, 60.0)
        x, y = dist * math.cos(bearing), dist * math.sin(bearing)
        vx, vy = -speed * math.cos(bearing), -speed * math.sin(bearing)
        alt = self.rng.uniform(80.0, 220.0)
        self.spawned += 1
        self.radar.add_target(TargetInit(f"src{self.spawned}", (x, vx, y, vy, alt, 0.0)))

    def step(self) -> None:
        self.k += 1
        if self.spawned < self.args.max_drones and self.k >= self.next_spawn:
            self._spawn_one()
            self.next_spawn = self.k + self.rng.randint(self.args.spawn_min, self.args.spawn_max)

        dets = self.radar.scan(T0 + timedelta(seconds=self.k))
        self.cur_dets = [(d.position[0], d.position[1]) for d in dets]
        self.live = self.tracker.process(dets, float(self.k))

        live_ids = set()
        for tr in self.live:
            live_ids.add(tr.track_id)
            if tr.track_id not in self.labels:            # newly classified -> name + colour
                idx = len(self.labels)
                self.labels[tr.track_id] = (
                    f"Shahed {LETTERS[idx % len(LETTERS)]}",
                    PALETTE[idx % len(PALETTE)],
                )
                self.trails[tr.track_id] = deque(maxlen=50)
            self.trails[tr.track_id].append((tr.position[0], tr.position[1]))

        for tid in list(self.trails):                     # retire dropped tracks
            if tid not in live_ids:
                del self.trails[tid]

    # -- drawing -------------------------------------------------------------

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

        # defended target + range rings
        ax.plot(0, 0, marker="*", color="red", markersize=20, zorder=6)
        for r in range(1000, lim + 1, 1000):
            ax.add_patch(plt.Circle((0, 0), r, fill=False, ls="--",
                                    color="#33405e", alpha=0.7))

        # raw, unidentified detections this scan
        if self.cur_dets:
            dx, dy = zip(*self.cur_dets)
            ax.scatter(dx, dy, s=18, color="#9aa7c7", alpha=0.45,
                       edgecolors="white", linewidths=0.3, zorder=3)

        # confirmed, classified threats — colour + trail + label
        for tr in self.live:
            name, colour = self.labels[tr.track_id]
            trail = self.trails[tr.track_id]
            if len(trail) > 1:
                tx, ty = zip(*trail)
                ax.plot(tx, ty, color=colour, lw=1.6, alpha=0.8, zorder=4)
            x, y = tr.position[0], tr.position[1]
            ax.plot(x, y, marker="o", color=colour, ms=9, zorder=5,
                    markeredgecolor="white", markeredgewidth=0.6)
            rng_m = math.hypot(x, y)
            ax.annotate(f"{name}  ({rng_m:.0f} m)", (x, y),
                        textcoords="offset points", xytext=(9, 5),
                        color=colour, fontsize=9, fontweight="bold")

        active = ", ".join(name for name, _ in self.labels.values()
                           if name in {n for n, _ in (self.labels[t.track_id] for t in self.live)}) or "—"
        ax.set_title(
            f"t = {self.k:>3d} s    detections: {len(self.cur_dets)}    "
            f"classified threats: {len(self.live)}    [{active}]",
            color="white", fontsize=11,
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--interval", type=int, default=200, help="ms per frame (animation speed)")
    p.add_argument("--max-drones", type=int, default=6)
    p.add_argument("--spawn-min", type=int, default=5, help="min sim-seconds between spawns")
    p.add_argument("--spawn-max", type=int, default=14, help="max sim-seconds between spawns")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--prob-detect", type=float, default=0.95)
    p.add_argument("--position-noise", type=float, default=8.0)
    p.add_argument("--cull", type=float, default=5500.0, help="range (m) past which drones are dropped")
    p.add_argument("--limit", type=int, default=4500, help="map half-width (m)")
    p.add_argument("--headless", action="store_true", help="no window; run --frames ticks -> PNG")
    p.add_argument("--frames", type=int, default=60, help="ticks to run in --headless")
    p.add_argument("--out", default="viz/live_tracker.png")
    args = p.parse_args()

    app = LiveTracker(args)

    if args.headless:
        fig, ax = plt.subplots(figsize=(9, 9))
        for _ in range(args.frames):
            app.step()
        app.render(ax)
        fig.savefig(args.out, dpi=120, facecolor="#0b1021")
        print(f"[headless] ran {args.frames} ticks, classified {len(app.labels)} threats -> {args.out}")
        return

    matplotlib.use("macosx", force=True)
    from matplotlib.animation import FuncAnimation

    fig, ax = plt.subplots(figsize=(9, 9))
    fig.patch.set_facecolor("#0b1021")

    def update(_frame):
        app.step()
        app.render(ax)

    # keep a reference so the animation isn't garbage-collected
    ani = FuncAnimation(fig, update, interval=args.interval, cache_frame_data=False)
    fig._ani = ani  # noqa: SLF001 — pin it to the figure
    plt.show()


if __name__ == "__main__":
    main()
