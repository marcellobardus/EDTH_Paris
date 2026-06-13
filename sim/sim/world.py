"""Entry point: launch Gazebo headless + gz-launch websocket bridge for gzweb."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_WORLD_TEMPLATE = Path(__file__).parent.parent / "worlds" / "intercept_scenario.sdf"
_ASSETS = Path(__file__).parent.parent / "worlds" / "assets"
_WEBSOCKET_LAUNCH = "/usr/share/gz/gz-launch7/configs/websocket.gzlaunch"


def main() -> None:
    sdf = _WORLD_TEMPLATE.read_text()

    with tempfile.NamedTemporaryFile(
        suffix=".sdf", delete=False, mode="w", prefix="intercept_"
    ) as tmp:
        tmp.write(sdf)
        world_path = tmp.name

    # Register the assets directory as a valid Gazebo resource path so the
    # WebSocket server's allowlist check permits serving the terrain textures.
    env = os.environ.copy()
    existing = env.get("GZ_SIM_RESOURCE_PATH", "")
    env["GZ_SIM_RESOURCE_PATH"] = (
        f"{_ASSETS}:{existing}" if existing else str(_ASSETS)
    )

    # gz-sim-websocket-server-system was removed in Gazebo Harmonic; the
    # websocket bridge is now a gz-launch plugin run as a companion process.
    ws = subprocess.Popen(["gz-launch", _WEBSOCKET_LAUNCH], env=env)
    sim = subprocess.Popen([
        "gz", "sim",
        "-s",   # server only (no GUI — gzweb provides visualization)
        "-r",   # start running immediately
        world_path,
    ], env=env)
    try:
        sys.exit(sim.wait())
    finally:
        ws.terminate()
        Path(world_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
