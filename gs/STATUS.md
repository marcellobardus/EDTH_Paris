# Team 2 — Ground Station (Pre-Launch Intelligence)

_Status recap — 2026-06-13 · branch `feat/kalman-tracker`_

The Ground Station owns the **pre-launch** phase: turn the radar detection
stream into a clean threat picture and initial interceptor→track assignments,
then hand off (GS role ends at launch).

Spec'd pipeline (`CLAUDE.md` / workstreams G1–G4):
`/radar/detections → /gs/tracks → /gs/threats → /gs/assignments → interceptors`

## Milestone status

| # | Milestone | Status | Where |
|---|---|---|---|
| **G1** | Kalman filter bank on `/radar/detections` → `/gs/tracks` | ✅ done | `predictor.py`, `filter.py` |
| **G2** | Track manager: birth/death (gating, coast-then-drop) | ✅ done | `tracker.py`, `track_publisher.py` |
| **G3** | Threat scorer on `/gs/tracks` → `/gs/threats` | ⬜ planned | `THREAT_SCORER_PLAN.md` |
| **G4** | Hungarian optimizer → `/gs/assignments` (≤ 2 s) | ⬜ planned (greedy mock only) | `mock_assignments.py` |

**G1 + G2 are built, tested, and verified end-to-end.** The multi-target tracker
fuses noisy detections into identity-stable `Track`s — empirically within a few
metres of ground truth, with stable IDs through crossings and missed scans.
27 GS tests pass.

## What's implemented

- **Tracker stack** (Stone Soup, manual per-scan loop): `predictor.py` (CV Kalman
  predict) → `filter.py` (predict+update) → `tracker.py` (gate → associate →
  update/coast → delete → initiate) → `track_publisher.py` (bus bridge,
  publishes `Track` on `/gs/tracks`).
- **Runnable node** `gs_node.py` — ZeroMQ; consumes `/radar/detections`, emits
  `/gs/tracks`, logs a per-scan track summary.
- **Assignment mock** `mock_assignments.py` — synthetic `Assignment`s so Team 3
  can develop while G3/G4 land.
- **Legacy** `launch_decider.py` — naive NN placeholder, superseded by the
  tracker; kept for its tests.

## Visualization (Team 4 tooling, lives in `viz/`)

- `viz/live_tracker.py` — **1-process** live map: runs radar + tracker in-process,
  shows blips getting classified as Shahed A/B/C… in real time (`macosx` backend).
- `viz/track_viewer.py` — **distributed** live map: subscribes to `/gs/tracks`
  over ZMQ; the 4th process behind `world_node → radar_sensor_node → gs_node`.
- `viz/plot_drone_paths.py` — static post-run plot (true paths vs detections vs
  fused tracks; range-to-target over time).

## Next up

- **G4 first, with a stubbed threat score**, then **G3** dropped in behind it —
  G4 is the load-bearing milestone (produces `/gs/assignments`, unblocks Team 3),
  runs on a flat score today, and G3 can only be tuned by watching its effect on
  assignments. See `THREAT_SCORER_PLAN.md` for the G3 design (and the G4 cost
  contract it must feed: `C[i][j] = intercept_time / threat_score`).

## Integration notes

- `numpy` / `scipy` / `stonesoup` already in `gs/pyproject.toml` — G4 needs no new
  deps.
- The faithful run is `world_node → radar_sensor_node → gs_node`, where
  `world_node` is a drop-in stand-in for Jules' Gazebo on `/simulation/ground_truth`.
- This branch also carries the **app-layer message security** layer
  (`contracts/contracts/security.py`, always-on AEAD `seal/unseal`).
