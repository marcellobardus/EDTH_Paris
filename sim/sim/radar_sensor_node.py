"""
Continuously-running radar-sensor node.

The *radar*: a pure sensor. It subscribes to the world's true drone poses on
``Topics.GROUND_TRUTH``, runs each through a Stone-Soup measurement model
(Gaussian position noise + detection probability), and publishes the noisy
``RadarDetection`` hits on ``Topics.RADAR_DETECTIONS``. The measurement noise
lives here, in the radar, because that is the sensor's physical imperfection —
the world supplies exact truth.

Two ZeroMQ endpoints, one per pub/sub channel (a single ``ZmqBus`` binds both its
sockets to one address, which would collide): it binds SUB on ``--truth-addr``
(ground truth in, the world connects) and connects PUB to ``--detections-addr``
(detections out, the ground station binds).

When Jules' Gazebo replaces ``world_node``, this node is unchanged — only the
source publishing ``/simulation/ground_truth`` differs.

    # one terminal each, downstream first:
    uv run python -m gs.gs_node                  # ground station (binds detections + tracks)
    uv run python -m sim.radar_sensor_node       # the radar
    uv run python -m sim.world_node --transport zmq   # the drones
"""

from __future__ import annotations

import argparse
import logging
import math

from contracts.bus import ZmqBus
from contracts.messages import GroundTruth, GroundTruthObject
from contracts.topics import Topics

from sim.radar_sensor import RadarSensor

log = logging.getLogger("radar")


def _rehydrate(ground_truth: GroundTruth) -> GroundTruth:
    """Rebuild nested ``GroundTruthObject`` instances after a JSON round-trip.

    ``ZmqBus`` serialises with ``asdict`` (recursive) but deserialises with
    ``GroundTruth(**data)``, which leaves ``objects`` as plain dicts — fine for
    flat messages, but ``GroundTruth`` nests dataclasses. Reconstruct them so the
    sensor can read ``obj.kind`` / ``obj.position`` etc. (In-process ``MockBroker``
    passes objects through untouched, so this is a no-op there.)
    """
    objects = [
        obj if isinstance(obj, GroundTruthObject) else GroundTruthObject(**obj)
        for obj in ground_truth.objects
    ]
    return GroundTruth(objects=objects, timestamp=ground_truth.timestamp)


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous radar-sensor node")
    parser.add_argument(
        "--truth-addr", default="tcp://127.0.0.1:5555", help="ground-truth SUB address"
    )
    parser.add_argument(
        "--detections-addr", default="tcp://127.0.0.1:5556", help="detections PUB address"
    )
    parser.add_argument("--prob-detect", type=float, default=0.9, help="detection probability")
    parser.add_argument("--noise", type=float, default=5.0, help="position noise std (m)")
    parser.add_argument("--seed", type=int, default=0, help="RNG seed")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    truth_in = ZmqBus(args.truth_addr, bind=True)  # SUB binds; the world connects
    detections_out = ZmqBus(args.detections_addr, bind=False)  # PUB connects; the GS binds
    sensor = RadarSensor(
        detections_out,
        "radar1",
        position_noise_m=args.noise,
        prob_detect=args.prob_detect,
        seed=args.seed,
    )

    def on_truth(ground_truth: GroundTruth) -> None:
        ground_truth = _rehydrate(ground_truth)
        dets = sensor.observe(ground_truth)  # applies noise + publishes detections
        if dets:
            ranges = sorted(round(math.dist((0.0, 0.0, 0.0), d.position)) for d in dets)
            log.info(
                "t=%6.1fs  detected %d/%d  TX on %s  ranges=%sm",
                ground_truth.timestamp,
                len(dets),
                len(ground_truth.objects),
                Topics.RADAR_DETECTIONS,
                ranges,
            )
        elif ground_truth.objects:
            log.info(
                "t=%6.1fs  detected 0/%d (all missed this scan)",
                ground_truth.timestamp,
                len(ground_truth.objects),
            )

    truth_in.subscribe(Topics.GROUND_TRUTH, GroundTruth, on_truth)

    log.info(
        "radar sensor: %s (%s) -> %s (%s), p_detect=%g noise=%gm. Ctrl-C to stop.",
        Topics.GROUND_TRUTH,
        args.truth_addr,
        Topics.RADAR_DETECTIONS,
        args.detections_addr,
        args.prob_detect,
        args.noise,
    )
    try:
        while True:
            truth_in.spin(timeout_ms=200)
    except KeyboardInterrupt:
        log.info("radar sensor stopped.")


if __name__ == "__main__":
    main()
