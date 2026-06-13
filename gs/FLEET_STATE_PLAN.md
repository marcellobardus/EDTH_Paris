# Plan: GS interceptor fleet-state manager (`gs/`)

> Design + spec for the ground station's model of its **own** interceptors — how
> many, where, and what state each is in. This is the input layer the G4
> optimizer depends on (`HUNGARIAN_OPTIMIZER_PLAN.md`): the optimizer asks the
> fleet "which units are available, and where?". Grounded in the real contracts:
> `InterceptorConfig` (`contracts/contracts/config.py`), `InterceptorState` +
> `EngagementEvent` (`contracts/contracts/messages.py`), and `Topics`.

**Not a workstreams milestone of its own** — it's the missing *input* to G4
(see the "Required input data — the interceptor inventory" section of the G4
plan). Without it the optimizer has no pool to assign.

## What it is

A single source of truth for the interceptor fleet, owned by the GS. It is
initialised from config (the pre-launch pad layout) and kept current from two
live streams (each interceptor's own state broadcast, and engagement outcomes).
Everything downstream — the optimizer, the viewer, metrics — reads the fleet
instead of re-deriving "what do we have left and where".

```
config ─┐
        ├─► InterceptorFleet ──available()──► G4 optimizer
/interceptors/{id}/state ─┤        └──snapshot()──► viewer / metrics
/simulation/engagement ───┘
```

## Decision taken: positions are GS-side (no contract change)

`InterceptorConfig` has a single shared `launch_position` + `count`. A co-located
fleet makes the optimizer degenerate (every cost-matrix row identical), so we need
**distinct** positions — resolved **on the GS side**, with no `contracts/` change
(no team sign-off needed):

- **Default: a defensive ring.** Place the `count` units evenly on a circle of
  radius `ring_radius` (a GS-side param, e.g. 300 m) centred on the configured
  `launch_position`. Distinct, deterministic, non-degenerate.
- **Override: an explicit list.** `from_config(cfg, positions=[...])` takes
  caller-supplied sites verbatim (for hand-placed batteries / tests).

If/when the team wants authoritative per-unit sites, this promotes cleanly to a
`launch_positions: list[Vec3]` contract field later — the fleet's internals don't
change, only where `positions` comes from.

## State model

```python
class Status(Enum):
    READY      # at its site, assignable by the optimizer
    ASSIGNED   # committed to a track pre-launch, not yet launched
    IN_FLIGHT  # launched and pursuing (its state broadcast has been seen)
    EXPENDED   # engagement resolved — kill or miss
    DOWN       # lost / offline (alive == False)

@dataclass
class FleetUnit:
    interceptor_id: str
    position: Vec3            # its site pre-launch; live position once in flight
    velocity: Vec3
    speed_mps: float
    range_m: float
    status: Status
    assigned_track_id: str | None
    alive: bool
    last_update: float        # scenario seconds of the last state touch
```

## Inputs → transitions (the lifecycle)

| Trigger | Source | Transition / effect |
|---|---|---|
| init | `InterceptorConfig` (+ ring/explicit positions) | create `count` units → **READY** at distinct sites |
| `mark_assigned(id, track)` | G4 commit (assignment publisher) | **READY → ASSIGNED**, record `assigned_track_id` |
| `on_interceptor_state(s)` | `/interceptors/{id}/state` (5 Hz) | update pos/vel/assigned/alive; **ASSIGNED → IN_FLIGHT** on first broadcast; `alive=False` → **DOWN** |
| `on_engagement(e)` | `/simulation/engagement` | **IN_FLIGHT → EXPENDED** (terminal; kill or miss) |

Edge rules: a state/engagement for an **unknown id** is logged and ignored (the
GS only manages units it was configured with). Transitions are monotonic toward
terminal states — an `EXPENDED`/`DOWN` unit never returns to `READY` in a single
scenario. `available()` is exactly the set of `READY` units.

## Architecture: state object + thin bus wiring

Mirrors the rest of `gs/`: the **`InterceptorFleet`** is a self-contained state
object with plain methods (`mark_assigned`, `on_interceptor_state`,
`on_engagement`, `available`, `snapshot`, `counts`) — **no bus inside it**, so it
is deterministic and unit-testable. A node wires the bus to its handlers
(subscribe to `Topics.ENGAGEMENT` and, per configured id,
`Topics.interceptor_state(id)`), exactly as `track_publisher` wires the tracker.

The fleet is **shared** between the assignment node and the state subscriptions:
the assignment publisher calls `fleet.available()` to get the pool, runs the
optimizer, then `fleet.mark_assigned(id, track)` for each committed pair.

## Phases

**Phase 0 — Nothing new.** No deps (`contracts` only). No contract change.

**Phase 1 — Fleet state object** (`gs/gs/fleet.py`)
- `Status`, `FleetUnit`, `InterceptorFleet` with `from_config` (ring/explicit
  positions), the transition methods, and `available()` returning the optimizer's
  `Interceptor` shape. Pure; ships with unit tests.

**Phase 2 — Bus wiring** (in the assignment node / `gs_node.py`)
- Subscribe `ENGAGEMENT` and per-id `interceptor_state(...)` to the fleet's
  handlers. Log fleet `counts()` per tick so the lifecycle is observable.

**Phase 3 — Tests** (`gs/tests/test_fleet.py`)
- Unit tests (acceptance below), plus a small lifecycle integration over a
  `MockBroker`: publish a few `InterceptorState` + an `EngagementEvent`, assert
  the unit walks READY → ASSIGNED → IN_FLIGHT → EXPENDED and drops out of
  `available()` at the right moments.

---

# Spec: `InterceptorFleet` (`gs/gs/fleet.py`)

### Interface

```python
class InterceptorFleet:
    @classmethod
    def from_config(
        cls,
        cfg: ScenarioConfig,
        *,
        ring_radius: float = 300.0,
        positions: list[Vec3] | None = None,   # explicit sites override the ring
    ) -> "InterceptorFleet": ...

    def available(self) -> list[Interceptor]:        # READY units, optimizer shape
    def mark_assigned(self, interceptor_id: str, track_id: str) -> None:
    def on_interceptor_state(self, state: InterceptorState) -> None:
    def on_engagement(self, event: EngagementEvent) -> None:
    def snapshot(self) -> list[FleetUnit]:           # full fleet, for viz/metrics
    def counts(self) -> dict[Status, int]:           # how many in each state
```

### Behavioral contract

- **Determinism:** ring positions are a pure function of `count`, `ring_radius`,
  and `launch_position` — same config ⇒ same sites, every run.
- **`available()` is authoritative:** returns only `READY` units; an assigned,
  launched, expended, or down unit is never offered to the optimizer.
- **Monotonic lifecycle:** transitions only advance toward terminal
  (`EXPENDED`/`DOWN`); no resurrection within a scenario.
- **Unknown ids ignored:** live messages for ids not in the configured fleet are
  logged and dropped — never auto-create units.
- **No I/O:** the object never touches the bus; a node feeds its handlers.

### Acceptance tests (`gs/tests/test_fleet.py`)

1. **Init:** `count=4` ⇒ 4 `READY` units at 4 **distinct** positions, each at
   `ring_radius` from `launch_position`.
2. **Explicit positions override** the ring verbatim.
3. **`available()` filters by status:** only `READY` units returned; after
   `mark_assigned` the pool shrinks by one.
4. **Assign → launch:** `mark_assigned` ⇒ `ASSIGNED`; first `InterceptorState`
   ⇒ `IN_FLIGHT` with updated position/velocity.
5. **Engagement is terminal:** `EngagementEvent` ⇒ `EXPENDED`; unit leaves
   `available()` and never returns.
6. **Loss:** `InterceptorState(alive=False)` ⇒ `DOWN`, not available.
7. **Unknown id:** state/engagement for an unconfigured id ⇒ ignored, no error,
   fleet unchanged.
8. **`counts()`** reflects the status distribution through a full lifecycle.
9. **`available()` shape** matches `optimizer.Interceptor` (id, position, speed,
   range) so it drops straight into G4.

### Out of scope

The assignment algorithm (G4), threat scoring (G3), interceptor guidance / the
agents that *produce* `InterceptorState` (Team 3), and re-arming/multi-wave
inventory. This component only *reflects* fleet state; it does not command it.

## Open questions (low-stakes, with defaults)

1. **Ring radius source** — GS-side constant vs a new config knob. *Default:
   `from_config` param (300 m); no contract change.*
2. **What marks "launched"** — first `InterceptorState` after `ASSIGNED`, vs an
   explicit launch signal. *Default: first state broadcast ⇒ IN_FLIGHT.*
3. **Pre-launch-only vs full lifecycle** — *Default: full lifecycle as specced
   (config + live state + engagement), since the GS picture is most useful end to
   end and the viewer can render it.*
4. **Stale IN_FLIGHT units** — expire on missing broadcasts like the track viewer
   does? *Default: no — engagement is the terminal signal; add a watchdog only if
   needed.*

## Appendix: how it slots into G4

```
InterceptorFleet.available()  ─►  AssignmentOptimizer.assign(threats, interceptors)
        ▲                                        │
        └──────── fleet.mark_assigned(id, track) ◄┘  (after the solve commits)
```

`available()` returns exactly the `list[Interceptor]` the optimizer consumes, so
this plan **supersedes the `build_interceptors` adapter** sketched in the G4 plan
— the fleet *is* that adapter, with live state attached. Build order: this fleet
object (Phase 1) → G4 optimizer Phase 1 → wire both into the assignment node.
