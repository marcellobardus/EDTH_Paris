"""Integration: Stone-Soup radar -> MockBus -> ground-station launch decision."""

from __future__ import annotations

from datetime import datetime, timedelta

from agent.bus import MockBroker
from gs.launch_decider import LaunchDecider
from sim.radar_stonesoup import StoneSoupRadar, TargetInit

T0 = datetime(2026, 6, 13, 12, 0, 0)

# Four well-separated targets (> gate apart) closing on the origin from N/E/S/W.
_TARGETS = [
    TargetInit("north", (0.0, 0.0, 2000.0, -50.0, 100.0, 0.0)),
    TargetInit("east", (2000.0, -50.0, 0.0, 0.0, 100.0, 0.0)),
    TargetInit("south", (0.0, 0.0, -2000.0, 50.0, 100.0, 0.0)),
    TargetInit("west", (-2000.0, 50.0, 0.0, 0.0, 100.0, 0.0)),
]


def _run(targets: list[TargetInit], pool: int, scans: int = 6) -> LaunchDecider:
    broker = MockBroker()
    decider = LaunchDecider(broker.endpoint("gs"), interceptor_pool=pool)
    radar = StoneSoupRadar(broker.endpoint("radar1"), "radar1", targets, start_time=T0, seed=1)
    for k in range(1, scans + 1):
        radar.scan(T0 + timedelta(seconds=k))
    return decider


def test_each_new_threat_triggers_one_launch() -> None:
    decider = _run(_TARGETS[:2], pool=3)
    assert decider.threats_seen == 2
    assert decider.interceptors_committed == 2
    assert [d.launched for d in decider.decisions] == [True, True]


def test_repeated_detections_do_not_relaunch() -> None:
    # Two targets, many scans -> still exactly two launch decisions.
    decider = _run(_TARGETS[:2], pool=3, scans=10)
    assert len(decider.decisions) == 2


def test_pool_exhaustion_holds_extra_threats() -> None:
    decider = _run(_TARGETS, pool=3)
    assert decider.threats_seen == 4
    assert decider.interceptors_committed == 3        # only 3 interceptors available
    launched = [d for d in decider.decisions if d.launched]
    held = [d for d in decider.decisions if not d.launched]
    assert len(launched) == 3
    assert len(held) == 1
    assert held[0].interceptor_id is None
    assert "hold" in held[0].reason
