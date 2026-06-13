"""Entry point: launch Gazebo headless + gz-launch websocket bridge for gzweb."""

import subprocess
import sys
from pathlib import Path

WORLD = Path(__file__).parent.parent / "worlds" / "quadrotor.sdf"
WEBSOCKET_LAUNCH = "/usr/share/gz/gz-launch7/configs/websocket.gzlaunch"


def main() -> None:
    # gz-sim-websocket-server-system was removed in Gazebo Harmonic; the
    # websocket bridge is now a gz-launch plugin run as a companion process.
    ws = subprocess.Popen(["gz-launch", WEBSOCKET_LAUNCH])
    sim = subprocess.Popen([
        "gz", "sim",
        "-s",   # server only (no GUI — gzweb provides visualization)
        "-r",   # start running immediately
        str(WORLD),
    ])
    try:
        sys.exit(sim.wait())
    finally:
        ws.terminate()


if __name__ == "__main__":
    main()
