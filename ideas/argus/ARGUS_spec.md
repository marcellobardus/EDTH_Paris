# ARGUS — Attested Real-time Ground & Unmanned Sensing

**Project spec — European Defense Tech Hackathon, Paris, June 12–14 2026**
**Concept:** A Palantir-style Common Operating Picture (COP) for low-cost distributed sensors, where every reading is cryptographically signed, replay-protected, and logged in a tamper-evident chain — and where cryptographic identity is only admission control: tracks become actionable only when independent sensors physically corroborate the claim.

---

## 1. Thesis

Existing COPs (Lattice, Gaia, Delta) assume a trusted network perimeter. That assumption collapses when your sensor layer is hundreds of €30 commodity nodes scattered in fields, reachable by the adversary, and feeding engagement decisions.

ARGUS treats trust as **two independent claims that must never be conflated**:

1. **Authenticity** (cryptography): *who sent this reading, was it tampered with, is it fresh?*
2. **Confirmation** (physics): *is the claim true?* A captured or coerced node signs perfect lies — valid signature, false world.

Authentication is admission control; corroboration decides what you actually trust. A track is firing-quality only when green on **both** axes.

Pitch line: *"Authentication tells you who sent a reading. It can't tell you the reading is true — a captured sensor signs perfect lies. ARGUS verifies the messenger, then cross-examines the claim."*

## 2. Goals / Non-goals

**Goals (must demo Sunday):**

- G1. Live map fusing ≥3 sensor feeds (≥1 real hardware node, rest simulated) into correlated tracks.
- G2. Two-axis trust on every track: **Authenticity** (`VERIFIED`/`UNVERIFIED`/`FAILED`) × **Confirmation** (`UNCONFIRMED`/`CONFIRMED`/`DISPUTED`), both displayed.
- G3. Live red-team demo, four attacks: ghost node, impersonation, replay — and the money shot, `spoof-authentic`: a cryptographically perfect lie from a "captured" enrolled node, caught by corroboration and marked `DISPUTED`.
- G4. Append-only hash-chained log with checkpointing; on-stage proof that history can't be silently rewritten.
- G5. The real hardware node's live detection visibly reaching `CONFIRMED` via overlapping simnode coverage — corroboration demonstrated, not just described.

**Non-goals (pathway slides only):**

- Production track fusion (multi-hypothesis tracking, clutter rejection).
- Key distribution at scale, key rotation, revocation.
- Post-quantum signatures, hardware secure elements.
- Real RF/radar integration.
- Blockchain anchoring of log checkpoints (cut from build — live-network dependency for a non-core feature; one roadmap slide).
- Full RFC 6962 Merkle tree (hash chain makes the same on-stage point).
- BFT/consensus between nodes.

## 3. Architecture

```
┌─────────────┐   signed readings    ┌──────────────┐
│ Sensor node │ ───── MQTT/WS ────►  │   Gateway    │
│ (ESP32, ×1  │                      │  - verify    │
│  real)      │                      │  - dedup     │
└─────────────┘                      │  - Merkle log│
┌─────────────┐                      └──────┬───────┘
│ Sim nodes   │ ────────┘                   │ verified events
│ (×5, Python)│                      ┌──────▼───────┐
└─────────────┘                      │ Fusion engine│
┌─────────────┐                      │  - associate │
│ Red-team    │ ── forged/replayed   │  - track mgmt│
│ injector    │    packets           └──────┬───────┘
└─────────────┘                             │ WebSocket
                                     ┌──────▼───────┐
                                     │  COP frontend │
                                     │  map + trust  │
                                     └──────────────┘
```

Five components, one repo, monorepo layout:

| Component | Stack | Owner-ish |
|---|---|---|
| `node-fw/` ESP32 firmware | C++ (Arduino) + Monocypher (Ed25519) | IoT person |
| `simnodes/` simulated sensors | Python, same envelope format | anyone |
| `gateway/` verify + log | Python FastAPI (or Go), SQLite | crypto person (you) |
| `fusion/` track engine | Python, runs inside gateway process | SWE |
| `cop/` frontend | React + MapLibre GL + deck.gl, WebSocket | SWE/frontend |
| `redteam/` attack CLI | Python | you, Saturday night |

Keep gateway+fusion one process. Two processes total (backend, frontend) plus nodes. Resist microservices.

## 4. Data model

### 4.1 Signed reading envelope

Canonical CBOR (or canonical JSON — sorted keys, no floats in signed portion; encode lat/lon as int microdegrees). Signature over the canonical bytes of `body`.

```
{
  "body": {
    "node_id":   "argus-node-07",
    "ctr":       1842,             // monotonic counter, persisted on node
    "ts":        1749900000123,    // ms epoch, node clock
    "kind":      "acoustic" | "visual" | "rf-sim",
    "detection": {
      "class":   "uas-shahed" | "uas-fpv" | "vehicle" | "unknown",
      "conf":    87,               // 0-100
      "lat_u":   48856613, "lon_u": 2352222,   // microdegrees
      "bearing": 214,              // optional, deg
      "alt_m":   120               // optional
    }
  },
  "sig": "<ed25519 sig, base64>",
  "kid": "<key id = first 8 bytes of pubkey hash>"
}
```

### 4.2 Track

Server-side object: `track_id`, fused position+velocity, class, contributing readings (by log index), and **two independent trust axes**:

```
auth   = VERIFIED      // all contributing readings: valid sig, fresh, counter-valid
       | UNVERIFIED    // unknown kid (not enrolled) — grey, never fused with verified data
       | FAILED        // bad sig, counter regression, stale ts — red, quarantined

confirm = UNCONFIRMED  // single node — displayed, never weapons-grade
        | CONFIRMED    // ≥k (k=2) enrolled nodes with distinct vantage corroborate,
                       // AND implied kinematics physically possible for the class
        | DISPUTED     // authenticated nodes that should agree, don't — or the
                       // claim violates physics/geometry. Valid signatures included.
```

Rules:

- Axes never mix: a FAILED reading can't downgrade a VERIFIED track — it spawns a quarantined ghost track. A perfect signature earns zero confirmation credit.
- Single-node detections **never** reach `CONFIRMED`, regardless of signature quality.
- **Firing-quality = `VERIFIED` + `CONFIRMED`.** Everything else is situational awareness at best.

This split is the thesis: `VERIFIED` answers "who sent it, intact, fresh" — it does not and cannot answer "is it true." Without the second axis, the most realistic attack on a cheap-sensor net (captured key signing lies) is invisible.

### 4.3 Tamper-evident log

- Every reading (any verdict) appended to SQLite: `(idx, hash, prev_root, envelope_blob, auth_verdict)`.
- Hash chain: `root_i = SHA-256(root_{i-1} || H(envelope_i))`. Deliberately not a full Merkle tree — same on-stage tamper-evidence, hours cheaper. "RFC 6962 tree + public anchoring" is a roadmap slide, not Saturday work.
- Checkpoint every 30 s: root printed to console and shown in the COP corner. Chain/OpenTimestamps anchoring: **slide only** — it's a live-network dependency on a non-core feature and reads as buzzword garnish to crypto-literate judges.

## 5. Crypto design

### 5.1 Primitives

- **Ed25519** signatures. ESP32: Monocypher (pure C, no heap surprises) or libsodium port. Server: PyNaCl.
- **SHA-256** for log hashing.
- No encryption of readings in v0 — confidentiality is a non-goal; integrity and authenticity are the product. Say this explicitly when asked, it shows judgment.

### 5.2 Enrollment

Hackathon-grade ceremony: node generates keypair on first boot, prints pubkey over serial; operator adds it to `gateway/enrolled_keys.json` with node metadata (declared position, sensor kind). Pathway slide: QR-based enrollment, certificates, revocation list, ATECC608 secure element so keys never leave hardware.

### 5.3 Anti-replay / freshness

- `ctr` strictly increasing per node; gateway stores last-seen counter; regression ⇒ FAILED.
- `ts` must be within ±N s of gateway clock (N=30 for demo; nodes NTP-sync on boot). Stale ⇒ FAILED with reason `STALE`.
- Counter persisted to ESP32 NVS so reboot doesn't reset it (round up to next 1000 on boot — classic trick).

### 5.4 Threat model (put this table in the pitch deck)

| Attack | Mechanism | ARGUS response |
|---|---|---|
| Ghost track injection | Forge readings from fake node | Unknown `kid` ⇒ UNVERIFIED, never fused |
| Node impersonation | Forge readings from real node id | Bad signature ⇒ FAILED, red |
| Replay | Re-send yesterday's real detection | Counter/timestamp ⇒ FAILED `REPLAY` |
| Tamper in transit | MITM modifies payload | Signature breaks ⇒ FAILED |
| History rewrite | Compromised gateway edits log | Hash-chain root mismatch vs published checkpoints |
| **Captured node** | Adversary extracts key, signs lies | **The confirmation axis exists for exactly this.** Reading passes every crypto check ⇒ `auth=VERIFIED`, but no independent node corroborates and/or the implied kinematics/geometry are impossible ⇒ `confirm=DISPUTED`, no firing solution. Demoed live via `spoof-authentic`. Residual limit (k colluding captured nodes with consistent geometry): secure elements + anomaly detection, roadmap. |

## 6. Sensor feeds

**Real node (×1, hero prop):** ESP32 + I2S MEMS mic. Don't attempt real classification — run a simple band-energy detector tuned to a Shahed audio recording played from a speaker (80–250 Hz harmonic energy over threshold ⇒ detection with conf proportional to energy). Position hardcoded. It exists so judges see physical hardware sign real data live.

**Simulated nodes (×5):** Python processes replaying a scripted scenario: two Shahed tracks crossing the Paris map, one FPV popping up near a "defended asset," background clutter (birds/vehicles, low conf). Each simnode has its own enrolled keypair and signs identically to hardware — gateway can't tell the difference, which is the point.

**Coverage requirement:** ≥2 simnodes' coverage must overlap the real node's declared position, scripted to corroborate its live detection — so the on-stage Shahed-audio moment visibly drives a track from `UNCONFIRMED` to `CONFIRMED` by independent sensors. With one hardware node, this is the only way to physically demo corroboration; it's a YAML edit, do it.

**Scenario file:** YAML timeline, deterministic, restartable — makes demo rehearsal sane.

## 7. Fusion engine (deliberately simple) + corroboration layer

**Fusion (unchanged, cheap):**

- Gating + greedy nearest-neighbor association: new reading joins a track if within `max(dist_gate, v_max × Δt)` and same/compatible class; else spawns a track.
- Track state: exponentially-weighted position+velocity (a real Kalman filter is ~40 lines if time permits Saturday; EWMA is fine and honest).
- Track expiry after 15 s without readings.
- Output: track table broadcast over WebSocket at 2 Hz.

**Corroboration layer (the actual innovation — three rules, all gating logic on state you already compute):**

1. **Independent corroboration.** `confirm = CONFIRMED` only when ≥2 enrolled nodes with distinct vantage (different `node_id`, declared positions ≥D apart) report compatible detections within the association gate. One node ⇒ `UNCONFIRMED`, displayed but never weapons-grade.
2. **Kinematic plausibility.** Per-class velocity/acceleration bounds checked against the EWMA velocity: a "Shahed" implying 600 km/h, or a teleport between frames ⇒ `DISPUTED`, signatures notwithstanding.
3. **Geometric consistency.** Where readings carry `bearing` (envelope already has the field), bearing lines from contributing nodes must intersect within tolerance of the claimed position. A lone node asserting a position no one else's geometry supports ⇒ `DISPUTED`.

`DISPUTED` is sticky for the track's lifetime until re-corroborated; the UI shows *why* (which rule fired). No Kalman, no BFT — a state field plus bounds checks inside the fusion process. See Appendix A for pseudocode.

## 8. COP frontend

- Dark theme, MapLibre GL with free dark tiles (Carto dark matter), Paris-centered. deck.gl layers: track icons (class glyph + velocity vector), node positions, detection rings.
- Trust rendering, two badges per track: **auth** (VERIFIED green / UNVERIFIED grey dashed / FAILED red pulsing) and **confirm** (CONFIRMED solid / UNCONFIRMED hollow / DISPUTED amber pulsing). Alert toasts carry the reason: `BAD SIGNATURE`, `REPLAY DETECTED`, `UNKNOWN KEY`, `NO CORROBORATION`, `KINEMATICS IMPOSSIBLE`, `GEOMETRY MISMATCH`. Firing-quality tracks (VERIFIED+CONFIRMED) get a distinct ring — the only ones an operator may act on.
- Side panel: live log tail with verdicts, current chain root, per-node health.
- One "operator action" to show decision-support: click a confirmed hostile track ⇒ shows time-to-asset and which nodes are tracking it. Cheap, looks operational.
- Keep it one page. Polish the map, skip auth/settings/everything else.

## 9. Demo script (4 min)

1. **(30 s)** Problem: cheap distributed sensors are the future of air defense; their data is unauthenticated AND uncorroborated — a spoofed track wastes an interceptor or masks a real raid.
2. **(60 s)** Live picture: speaker plays Shahed audio near the ESP32 → real node detects and signs → track appears `UNCONFIRMED` → two overlapping simnodes corroborate → badge flips to **VERIFIED + CONFIRMED** on screen. Chain root ticking in the corner.
3. **(60 s)** Red team, auth layer: `redteam.py inject-ghost` → grey UNVERIFIED, not fused. `redteam.py impersonate node-07` → red "BAD SIGNATURE." `redteam.py replay` → red "REPLAY DETECTED."
4. **(60 s)** **Money shot** — `redteam.py spoof-authentic`: a fully signed, counter-valid, fresh reading from a real enrolled node claiming a drone where none exists (captured key). Narrate: *"This one passes every cryptographic check — valid signature, correct counter, fresh timestamp. Every other system in this room, and the version of ARGUS that only checks signatures, would render this as a real inbound. But no other sensor sees it and the geometry it implies is impossible — so ARGUS marks it DISPUTED and refuses a firing solution. We don't just verify the messenger, we cross-examine the claim."*
5. **(30 s)** Tamper-evidence: published checkpoint vs recomputed root after a simulated log edit ⇒ mismatch.
6. **(15 s)** Pathway: secure elements, PQ signatures, k-of-n quorum tuning, public anchoring, NATO coalition angle (privacy-preserving cross-ally fusion as future work).

## 10. Build plan (Fri 18:00 → Sun 12:00, team of 3–4)

**Friday 18:00–24:00**
- Repo, envelope format frozen (do this FIRST — it's the contract between all components).
- Gateway skeleton: ingest, verify, SQLite log. Simnode v0 sending signed readings.
- Frontend: map renders, WebSocket plumbed.
- ESP32: blink + WiFi + MQTT publish hello-world.

**Saturday 09:00–14:00**
- Fusion engine + track broadcast. Frontend renders tracks with trust colors.
- ESP32: Monocypher signing working, counter in NVS.
- Scenario YAML + replay tooling.

**Saturday 14:00–20:00**
- **Corroboration layer**: two-axis state, k≥2 gating, kinematic bounds, bearing-intersection check (one person, see Appendix A).
- Hash-chain log + checkpoints.
- ESP32 mic: band-energy detector; calibrate against speaker.
- Red-team CLI: ghost / impersonate / replay / **spoof-authentic**.
- Scenario YAML: overlapping simnode coverage at the real node's position.

**Saturday 20:00–24:00**
- Integration pass, full demo run end-to-end. Freeze features at midnight.

**Sunday 09:00–12:00**
- Rehearse the demo ×3 with the actual speaker/audio. Pitch deck (threat-model table, pathway slides). Fix only demo-blocking bugs.

**Hard rule:** anything not demoable by Saturday midnight gets cut to a slide.

## 11. Risks & fallbacks

| Risk | Fallback |
|---|---|
| ESP32 audio flaky in noisy venue | Button on the node that triggers a (still genuinely signed) detection — "manual sensor trip." Crypto story intact. |
| ESP32 entirely dead | All-sim demo; hold up the board anyway, show serial output of it signing. |
| Venue WiFi unusable | Phone hotspot; everything runs on one laptop + hotspot LAN. Test this Friday. |
| Map tiles need internet | Pre-cache tiles or bundle a static Paris GeoJSON basemap. |
| Fusion looks janky | Slow the scenario down; fewer, cleaner tracks beat realistic clutter. |
| Corroboration rules misfire on legit tracks (false DISPUTED) | Tune gates against the deterministic scenario Saturday night; demo runs the rehearsed scenario, not improv. |
| spoof-authentic accidentally gets corroborated by scenario clutter | Inject it at a scripted quiet location/time in the YAML; rehearse. |

## 12. What makes this win

Differentiation against ~40 other teams: most will demo detection or a dashboard; some will demo authentication. ARGUS demos **an attack that passes every cryptographic check and still fails** — because trust is computed at the right layer. Authentication alone is the solved-ish third of the problem; the two-axis model directly answers the expert judge's "what about a node with a valid key that lies?" instead of conceding it. Live adversarial demos are rare and memorable, the scope is small enough to finish, and every component degrades gracefully.

---

## Appendix A — Corroboration layer pseudocode (start Friday night)

```python
# Per-class physical bounds (m/s). Tune Saturday.
BOUNDS = {
    "uas-shahed": {"v_max": 60,  "a_max": 10},
    "uas-fpv":    {"v_max": 50,  "a_max": 30},
    "vehicle":    {"v_max": 40,  "a_max": 8},
    "unknown":    {"v_max": 100, "a_max": 50},
}
K_CONFIRM      = 2      # distinct nodes required
MIN_VANTAGE_M  = 50     # nodes must be at least this far apart to count as independent
BEARING_TOL_M  = 150    # bearing lines must pass within this of fused position

def update_confirmation(track, readings_window):
    # readings_window: auth=VERIFIED readings associated to this track, last T seconds.
    # UNVERIFIED/FAILED readings never reach this function (admission control upstream).

    # Rule 2: kinematic plausibility (uses EWMA velocity you already compute)
    b = BOUNDS[track.cls]
    if track.speed > b["v_max"] or track.accel > b["a_max"]:
        return dispute(track, "KINEMATICS_IMPOSSIBLE")
    if track.frame_jump_m > b["v_max"] * track.dt + GATE_SLACK:   # teleport check
        return dispute(track, "KINEMATICS_IMPOSSIBLE")

    # Rule 3: geometric consistency (only for readings that carry bearing)
    for r in readings_window:
        if r.bearing is not None:
            if point_to_ray_distance(track.pos, node_pos(r.node_id), r.bearing) > BEARING_TOL_M:
                return dispute(track, "GEOMETRY_MISMATCH", offender=r.node_id)

    # Rule 1: independent corroboration
    nodes = {r.node_id for r in readings_window}
    if len(nodes) >= K_CONFIRM and max_pairwise_dist(nodes) >= MIN_VANTAGE_M:
        if track.confirm != DISPUTED:          # DISPUTED is sticky until re-corroborated
            track.confirm = CONFIRMED
    elif track.confirm != DISPUTED:
        track.confirm = UNCONFIRMED            # single node never reaches CONFIRMED

    return track

def dispute(track, reason, offender=None):
    track.confirm = DISPUTED
    track.dispute_reason = reason              # surfaced verbatim in the UI toast
    track.offender = offender                  # node under suspicion -> per-node health panel
    return track

def firing_quality(track):
    return track.auth == VERIFIED and track.confirm == CONFIRMED
```

Sticky-DISPUTED release (optional, if time): clear after N consecutive seconds of clean, k-corroborated, kinematically valid updates with the offending node excluded.

