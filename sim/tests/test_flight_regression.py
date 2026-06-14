"""Non-regression guard for the "make interceptors actually fly" fix (PR: commit
6e5342a, "Fly interceptors via Gazebo cmd_vel + fix cross-container DDS").

That PR fixed five interacting bugs that each silently broke flight. None of them
crash — they just make the interceptors sit still, fly backwards, or flip over,
so a normal test suite stays green while nothing moves. Real flight needs Gazebo,
which we can't run in CI, so instead we pin the *invariants* the fix established:

  1. cmd_vel is BODY-frame — the world velocity must be rotated by -yaw.
  2. _yaw_from_quat extracts heading from the gz pose quaternion.
  3. intercept_scenario.sdf uses `maxLinearAcceleration` (the misspelling
     `maximumLinearAcceleration` is silently ignored by gz -> unlimited accel
     -> the quad flips).
  4. Every cross-container DDS service shares `ipc: host` (else Fast DDS drops
     every sample over the SHM transport and agents freeze at launch).
  5. Scenario speeds stay inside the multicopter's stable envelope (~13 m/s).

If you are here because one of these failed: re-read the "Gotchas" section of
CLAUDE.md before "fixing" the test — it almost certainly means flight is broken.
"""

from __future__ import annotations

import math
from pathlib import Path

import yaml
from contracts.config import ScenarioConfig
from sim.driver import Interceptor, _yaw_from_quat

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SDF = _REPO_ROOT / "sim" / "worlds" / "intercept_scenario.sdf"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SCENARIO = _REPO_ROOT / "config" / "scenario_default.yaml"

# gzweb talks to the browser over a websocket, not DDS, so it is exempt from the
# ipc:host requirement (documented in CLAUDE.md).
_NON_DDS_SERVICES = {"base", "gzweb"}

# The multicopter controller flies stably only to ~11-13 m/s; faster commands
# tip it over. Allow a hair of margin over the tuned 13 m/s cap.
_QUAD_ENVELOPE_MPS = 13.5


# ── 1. cmd_vel must be world velocity rotated into the body frame (by -yaw) ────


def _make_itc(waypoint: tuple[float, float, float], yaw: float) -> Interceptor:
    itc = Interceptor("i1", (0.0, 0.0, 0.0), speed=13.0, max_turn_rate_deg_s=30.0)
    itc.waypoint = waypoint
    itc.yaw = yaw
    return itc


def test_body_command_when_facing_waypoint_is_pure_forward() -> None:
    """Heading straight at the waypoint => body command is pure +x (forward),
    no lateral component. This is the discriminating check: if the rotation sign
    were flipped (+yaw instead of -yaw) the command would point *backwards*."""
    for yaw, wp in [
        (0.0, (10.0, 0.0, 0.0)),  # facing +x, waypoint on +x
        (math.pi / 2, (0.0, 10.0, 0.0)),  # facing +y, waypoint on +y
        (math.pi, (-10.0, 0.0, 0.0)),  # facing -x, waypoint on -x
        (-math.pi / 2, (0.0, -10.0, 0.0)),  # facing -y, waypoint on -y
    ]:
        bx, by, _ = _make_itc(wp, yaw).body_velocity_command()
        assert bx > 0.0, f"yaw={yaw}: expected forward (+x) body command, got bx={bx}"
        assert abs(by) < 1e-9, f"yaw={yaw}: expected no lateral, got by={by}"


def test_body_command_rotates_by_negative_yaw() -> None:
    """Body velocity equals the world velocity rotated by -yaw, for arbitrary
    geometry where heading and bearing differ."""
    for yaw in [0.3, 1.1, -0.7, 2.5]:
        itc = _make_itc((30.0, 40.0, 0.0), yaw)
        wx, wy, wz = itc.desired_world_velocity()
        bx, by, bz = itc.body_velocity_command()
        c, s = math.cos(yaw), math.sin(yaw)
        assert math.isclose(bx, c * wx + s * wy, abs_tol=1e-9)
        assert math.isclose(by, -s * wx + c * wy, abs_tol=1e-9)
        assert bz == wz  # vertical is shared between frames


def test_body_command_preserves_horizontal_speed() -> None:
    """A frame rotation must not change magnitude — the controller would
    otherwise over/under-shoot the cruise speed."""
    itc = _make_itc((30.0, 40.0, 0.0), yaw=1.0)
    wx, wy, _ = itc.desired_world_velocity()
    bx, by, _ = itc.body_velocity_command()
    assert math.isclose(math.hypot(wx, wy), math.hypot(bx, by), rel_tol=1e-9)


def test_body_command_is_zero_without_waypoint() -> None:
    itc = Interceptor("i1", (0.0, 0.0, 0.0), speed=13.0, max_turn_rate_deg_s=30.0)
    itc.yaw = 1.0
    assert itc.body_velocity_command() == (0.0, 0.0, 0.0)


# ── 2. yaw extraction from the gz pose quaternion ──────────────────────────────


def test_yaw_from_quat_known_headings() -> None:
    # Yaw-only quaternion: (w, x, y, z) = (cos(a/2), 0, 0, sin(a/2)).
    for a in [0.0, 0.5, 1.0, -1.2, math.pi / 2]:
        q = (math.cos(a / 2), 0.0, 0.0, math.sin(a / 2))
        assert math.isclose(_yaw_from_quat(*q), a, abs_tol=1e-9)


# ── 3. SDF param names (a typo here = silently unlimited accel = flips) ────────


def test_sdf_uses_correct_acceleration_param_name() -> None:
    sdf = _SDF.read_text()
    assert "<maxLinearAcceleration>" in sdf, "missing accel cap => quad flips on launch"
    assert "maximumLinearAcceleration" not in sdf, (
        "the misspelling `maximumLinearAcceleration` is silently ignored by gz "
        "(unlimited accel -> flips). Use `maxLinearAcceleration`."
    )


def test_sdf_has_linear_velocity_cap() -> None:
    assert "<maximumLinearVelocity>" in _SDF.read_text(), "missing speed cap"


# ── 4. cross-container DDS services must share /dev/shm (ipc: host) ────────────


def test_all_dds_services_use_ipc_host() -> None:
    compose = yaml.safe_load(_COMPOSE.read_text())
    offenders = [
        name
        for name, svc in compose["services"].items()
        if name not in _NON_DDS_SERVICES and (svc or {}).get("ipc") != "host"
    ]
    assert not offenders, (
        f"DDS services missing `ipc: host`: {offenders}. Without it Fast DDS "
        "picks the SHM transport and silently drops every cross-container sample "
        "(agents freeze at launch). See CLAUDE.md Gotchas."
    )


# ── 5. scenario speeds stay inside the quad's stable flight envelope ──────────


def test_scenario_speeds_within_quad_envelope() -> None:
    cfg = ScenarioConfig.from_yaml(str(_SCENARIO))
    interceptor_speed = float(cfg.interceptors.speed_mps)
    shahed_top = float(max(cfg.shaheds.speed_mps))
    assert interceptor_speed <= _QUAD_ENVELOPE_MPS, (
        f"interceptor speed {interceptor_speed} m/s exceeds the multicopter's "
        f"~13 m/s stable envelope -> it tips over instead of chasing."
    )
    # Interceptors must out-run the threats or they can never close.
    assert shahed_top < interceptor_speed, (
        f"shahed top speed {shahed_top} m/s >= interceptor {interceptor_speed} m/s"
    )
