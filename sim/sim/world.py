"""Entry point: launch Gazebo headless (server only) with the quadrotor world."""

import subprocess
import sys
from pathlib import Path

WORLD = Path(__file__).parent.parent / "worlds" / "quadrotor.sdf"


def main() -> None:
    cmd = [
        "gz", "sim",
        "-s",           # server only (no GUI — gzweb provides visualization)
        "-r",           # start running immediately
        str(WORLD),
    ]
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
