#!/usr/bin/env python3
"""
Visualize incoming drones closing on the defended target — and how well the
ground station's tracker (G1/G2) follows them.

Runs the real pipeline we already have:

    StoneSoupRadar (truth + noisy detections)  ->  MultiTargetTracker (fused tracks)

and draws two panels:

  left  — top-down map: true drone paths, raw radar detections (noisy), and the
          tracker's fused estimate, all converging on the target at the origin.
  right — range-to-target vs time: every drone's distance shrinking as it closes
          in ("going towards us").

Usage:
    uv run python viz/plot_drone_paths.py
    uv run python viz/plot_drone_paths.py --drones 5 --scans 60 --seed 3
    uv run python viz/plot_drone_paths.py --prob-detect 0.8 --out /tmp/paths.png
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")  # headless: render straight to a file
import matplotlib.pyplot as plt

from agent.bus import MockBroker
from gs.tracker import MultiTargetTracker
from sim.radar_stonesoup import StoneSoupRadar, TargetInit

T0 = datetime(2026, 6, 13, 12, 0, 0)


def spawn_drones(n: int, rng: random.Random) -> list[TargetInit]:
    """n drones on random bearings 2.5–4 km out, each heading for the origin."""
    drones = []
    for i in range(n):
        bearing = rng.uniform(0, 2 * math.pi)
        dist = rng.uniform(2500.0, 4000.0)
        speed = rng.uniform(35.0, 60.0)
        x, y = dist * math.cos(bearing), dist * math.sin(bearing)
        vx, vy = -speed * math.cos(bearing), -speed * math.sin(bearing)  # toward origin
        alt = rng.uniform(80.0, 220.0)
        drones.append(TargetInit(f"D{i + 1}", (x, vx, y, vy, alt, 0.0)))
    return drones


def run_pipeline(drones, scans, prob_detect, position_noise, seed):
    """Drive radar -> tracker for `scans` ticks, recording everything to plot."""
    radar = StoneSoupRadar(
        MockBroker().endpoint("radar1"), "radar1", drones,
        start_time=T0, seed=seed, prob_detect=prob_detect,
        position_noise_m=position_noise,
    )
    tracker = MultiTargetTracker(start_time=T0)

    truth_paths: dict[str, list[tuple[float, float]]] = {d.target_id: [] for d in drones}
    truth_range: dict[str, list[tuple[float, float]]] = {d.target_id: [] for d in drones}
    detections_xy: list[tuple[float, float]] = []
    track_paths: dict[str, list[tuple[float, float]]] = {}

    for k in range(1, scans + 1):
        dets = radar.scan(T0 + timedelta(seconds=k))

        # ground truth (radar's hidden state) for each live drone
        for tid, gt in radar._truth.items():
            x, y, z = (float(gt.state_vector[i, 0]) for i in (0, 2, 4))
            truth_paths[tid].append((x, y))
            truth_range[tid].append((k, math.sqrt(x * x + y * y + z * z)))

        for d in dets:
            detections_xy.append((d.position[0], d.position[1]))

        for tr in tracker.process(dets, float(k)):
            track_paths.setdefault(tr.track_id, []).append((tr.position[0], tr.position[1]))

    return truth_paths, truth_range, detections_xy, track_paths


def plot(truth_paths, truth_range, detections_xy, track_paths, out: str) -> None:
    fig, (ax_map, ax_rng) = plt.subplots(1, 2, figsize=(15, 7))

    # ---- left: top-down map -------------------------------------------------
    # defended target + range rings
    ax_map.plot(0, 0, marker="*", color="red", markersize=22, zorder=5,
                label="defended target")
    for r in (1000, 2000, 3000):
        ax_map.add_patch(plt.Circle((0, 0), r, fill=False, ls="--",
                                    color="lightcoral", alpha=0.5))

    # true paths (grey) + spawn markers
    first = True
    for tid, pts in truth_paths.items():
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax_map.plot(xs, ys, color="grey", ls="--", lw=1, alpha=0.7,
                    label="true path" if first else None)
        ax_map.plot(xs[0], ys[0], marker="o", mfc="none", mec="black", ms=9)
        ax_map.annotate(tid, (xs[0], ys[0]), textcoords="offset points",
                        xytext=(6, 6), fontsize=9)
        first = False

    # raw noisy detections (light, scattered)
    if detections_xy:
        dx, dy = zip(*detections_xy)
        ax_map.scatter(dx, dy, s=8, color="steelblue", alpha=0.25, zorder=2,
                       label="radar detections (noisy)")

    # fused tracker estimates (one colour per track)
    cmap = plt.cm.viridis
    ids = sorted(track_paths)
    for n, tid in enumerate(ids):
        pts = track_paths[tid]
        xs, ys = zip(*pts)
        ax_map.plot(xs, ys, color=cmap(n / max(len(ids) - 1, 1)), lw=2,
                    marker=".", ms=3,
                    label="fused track" if n == 0 else None)

    ax_map.set_aspect("equal")
    ax_map.set_title("Top-down: incoming drones vs. fused tracks")
    ax_map.set_xlabel("x (m)")
    ax_map.set_ylabel("y (m)")
    ax_map.grid(True, alpha=0.3)
    ax_map.legend(loc="upper right", fontsize=9)

    # ---- right: range-to-target vs time ------------------------------------
    for tid, series in truth_range.items():
        if not series:
            continue
        ts, rs = zip(*series)
        ax_rng.plot(ts, rs, marker=".", label=tid)
    ax_rng.set_title("Range to target over time (closing in)")
    ax_rng.set_xlabel("time (s)")
    ax_rng.set_ylabel("distance to target (m)")
    ax_rng.grid(True, alpha=0.3)
    ax_rng.legend(fontsize=9)

    fig.suptitle("Ground-station tracking — drones closing on the defended target",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print(f"wrote {out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--drones", type=int, default=4)
    p.add_argument("--scans", type=int, default=60, help="1 s per scan")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--prob-detect", type=float, default=1.0)
    p.add_argument("--position-noise", type=float, default=8.0, help="radar 1σ, metres")
    p.add_argument("--out", default="viz/drone_paths.png")
    args = p.parse_args()

    rng = random.Random(args.seed)
    drones = spawn_drones(args.drones, rng)
    data = run_pipeline(drones, args.scans, args.prob_detect, args.position_noise, args.seed)
    plot(*data, args.out)


if __name__ == "__main__":
    main()
