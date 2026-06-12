# ARGUS / VERDICT — Target Validation & Intent Assessment

**Project spec — European Defense Tech Hackathon, Paris, June 12–14 2026**
**Challenge fit:** Alta Ares #3 — *Target Validation and Intent Assessment for Autonomous Engagement Decisions*
**Concept:** A reasoning engine that sits between *detection* and *the trigger*. It takes a track and renders an **engagement verdict** — `CLEAR-HOLD` / `ESCALATE` / `FIRING-QUALITY` — backed by three independent confidence-scored gates (Identity, Intent, Feasibility), a plain-language explanation of *why*, and an immutable record of the decision. It handles the routine, low-ambiguity cases autonomously and escalates exactly the ambiguous, high-consequence, time-critical ones to a human — which is the whole ask of Challenge #3.

> This is the ARGUS thesis pivoted from *"is this sensor reading authentic and corroborated?"* to *"is this target valid, what is its intent, and may we engage?"* — same architecture, reframed gates, a new reasoning layer on top.

---

## 1. Thesis

Challenge #3's own words: *"Detection alone is not enough — a radar blip could be friend, foe, or civilian."* Current ROE require a human to approve every shot — correct legally and ethically, but too slow for supersonic threats and saturation attacks.

ARGUS treats an engagement decision as **three independent claims that must never be conflated**, each answered separately and scored on its own confidence:

1. **Identity** — *who is this?* Friend / neutral / civilian / foe / unknown. (Registry & IFF correlation.)
2. **Intent** — *what are they doing?* Hostile, navigational error, or civilian emergency. (Trajectory, behavior, emissions.)
3. **Feasibility** — *may and can we engage?* Intercept geometry, collateral/airspace risk, ammunition, legal basis (ROE).

A target is **firing-quality only when all three gates are green with high confidence.** Any gate that is amber (ambiguous) or that carries high consequence routes the track to a human with the full reasoning laid out. The machine clears the obvious and escalates the hard — never the reverse.

**Pitch line:** *"Every other system tells you there's a blip. ARGUS tells you whether it's a threat, why it thinks so, and whether you're legally clear to act — and when it isn't sure, it hands you the case already made, not a raw radar return."*

## 2. Goals / Non-goals

**Goals (must demo Sunday):**

- G1. Live track picture (recorded/real ADS-B + scripted military tracks) on a COP, each track carrying three gate verdicts and an overall disposition.
- G2. The **three-gate reasoning engine**: deterministic evidence (geometry, registry match, kinematic bounds) → confidence per gate → disposition.
- G3. **Autonomous clear of the obvious**: a squawking civilian airliner is auto-resolved `CLEAR-HOLD` with no human action; a clean ballistic inbound at a defended asset is auto-promoted to `FIRING-QUALITY`.
- G4. **The money shot — escalation of the ambiguous**: an unknown subsonic, non-maneuvering, radar-silent aircraft from neutral territory (the challenge's own scenario) is marked `ESCALATE` with the competing hypotheses (neutral transport vs. masked threat vs. nav error) and the evidence for each, surfaced to the commander in seconds — not the 10+ minutes the challenge says diplomatic channels take.
- G5. **Explainability + accountability**: every verdict shows the factors that drove it, and every autonomous decision is written to a tamper-evident decision log for after-action review.

**Non-goals (pathway slides only):**

- Real radar/sensor integration (tracks come from ADS-B + scenario file).
- Production IFF / Mode 5 crypto, real classified registries.
- Weapon-system integration / actual fire control.
- Multi-target weapon–target assignment (that's challenge #2; this is per-track judgment).
- A learned/trained intent classifier from scratch — we use rules + an LLM reasoning layer over deterministic evidence, not a model we train this weekend.

## 3. Architecture

```
┌──────────────┐  tracks   ┌───────────────────────────────────────┐
│ Track source │ ────────► │           VERDICT ENGINE              │
│ - ADS-B feed │           │  ┌─────────┐ ┌────────┐ ┌───────────┐ │
│   (OpenSky)  │           │  │IDENTITY │ │ INTENT │ │FEASIBILITY│ │
│ - scenario   │           │  │ gate    │ │ gate   │ │ gate      │ │
│   YAML (mil) │           │  └────┬────┘ └───┬────┘ └─────┬─────┘ │
└──────────────┘           │       └──────────┼────────────┘       │
┌──────────────┐ registries│            ┌─────▼──────┐             │
│ Reference DBs│ ─────────►│            │ ADJUDICATOR │ confidence  │
│ - civ flights│           │            │  + LLM      │ + reason    │
│ - friendly   │           │            │  explainer  │             │
│ - ROE / geo  │           │            └─────┬──────┘             │
└──────────────┘           └──────────────────┼────────────────────┘
                                      decision │ + immutable log
                                       ┌───────▼────────┐
                                       │   COP frontend  │
                                       │ disposition card│
                                       │ + human override │
                                       └─────────────────┘
```

Components (monorepo, keep engine one process):

| Component | Stack | Does |
|---|---|---|
| `tracks/` source | Python | OpenSky/ADS-B ingest + scenario YAML replay → unified track stream |
| `registries/` | Python, static JSON/SQLite | civilian flight DB, friendly-force list, ROE geofences, known callsigns |
| `engine/` three gates + adjudicator | Python | deterministic evidence per gate → confidence → disposition |
| `reason/` LLM explainer | Mistral / Claude via API | synthesizes intent hypotheses + plain-language "why", **never holds fire authority** |
| `log/` decision record | SQLite, hash-chained | every verdict + evidence + disposition, append-only |
| `cop/` frontend | React + MapLibre GL + deck.gl | track map, disposition cards, one-click human override |
| `redteam/` scenario injector | Python | spoofed IFF, civilian-in-zone, decoy, ambiguity saturation |

## 4. The three gates

Each gate emits `(score 0–100, factors[], verdict ∈ {GREEN, AMBER, RED})`. Gates are independent; the adjudicator combines them.

### 4.1 Identity gate — *who is this?*
- Correlate track with **civilian flight DB** (ADS-B/OpenSky callsign, registration, route) → if matched and consistent ⇒ `CIVILIAN`.
- Check **friendly-force registry** / IFF transponder / known callsign ⇒ `FRIENDLY`.
- Check **transponder status**: squawking normal? 7500/7600/7700 (hijack/comms-fail/emergency)? silent?
- No match anywhere, transponder dark ⇒ `UNKNOWN` (the dangerous case — drives intent scrutiny).
- Output: identity class + confidence + which evidence matched.

### 4.2 Intent gate — *what are they doing?*
- **Trajectory toward defended asset?** Closing geometry, time-to-asset.
- **Kinematic profile vs. class** (reuse ARGUS's per-class bounds): supersonic + maneuvering = weapon-like; subsonic + level + steady = transport-like.
- **Emissions**: active radar? jamming? radio-silent? (radar-silent + closing = suspicious; squawking + on a filed airway = benign).
- **Behavioral anomalies**: altitude/speed changes, deviation from filed route, response to warnings.
- Output: P(hostile) vs P(nav-error) vs P(civilian-emergency) + the factors.

### 4.3 Feasibility gate — *may & can we engage?*
- **Intercept geometry**: is there a viable solution given interceptor envelope?
- **Collateral / airspace risk**: over populated area? near civilian air corridor? debris footprint?
- **Resources**: interceptor / ammunition availability.
- **Legal basis (ROE)**: inside an engagement-authorized zone? rules satisfied? — explicit, because #3 asks for "legal basis."
- Output: feasible? + risk score + ROE pass/fail + factors.

## 5. Adjudicator + reasoning layer (the engine)

**Deterministic core (authoritative):** the gates compute *hard* evidence — geometry, registry matches, kinematic bounds, ROE geofence checks. These are vetoes and facts, never guesses. A `CIVILIAN` registry match with a 7700 squawk is a hard `CLEAR-HOLD`; a supersonic inbound with a viable intercept inside an authorized zone and no civilian correlation is a hard `FIRING-QUALITY` candidate.

**LLM reasoning layer (advisory, explanatory):** for the *ambiguous middle*, an LLM (Mistral or Claude) is handed the structured evidence from all three gates and asked to (a) enumerate the competing intent hypotheses, (b) weigh them, (c) produce the commander-facing explanation. **It has zero fire authority** — it cannot promote a track to firing-quality; it can only inform an `ESCALATE` package or argue for `CLEAR-HOLD`. This keeps the hallucination-prone component out of the kill chain while using it for exactly what it's good at: synthesizing messy evidence into readable reasoning.

**Disposition logic:**
```
all gates GREEN, high confidence, no high-consequence flag → FIRING-QUALITY (autonomous)
identity CIVILIAN/FRIENDLY or intent clearly benign           → CLEAR-HOLD  (autonomous)
any gate AMBER, or high-consequence + any uncertainty,
  or hypotheses contested                                     → ESCALATE   (human, with the case pre-made)
identity RED-foe + intent hostile + feasible + ROE ok         → FIRING-QUALITY (autonomous, logged hard)
```
Hysteresis + a confidence floor prevent flapping; high-consequence dispositions are rate-limited and always logged.

## 6. Auditable decision log (accountability — why this matters more here than anywhere else)

Autonomous engagement decisions are legally and ethically loaded. ARGUS writes **every** verdict — autonomous or escalated — to an append-only, hash-chained SQLite log: `(idx, hash, prev_root, track_snapshot, gate_evidence, disposition, who_decided)`. Root checkpointed periodically and shown in the COP. On stage: prove the decision history can't be silently rewritten after the fact. This directly answers #3's *"explain reasoning — show commanders the factors driving autonomous decisions"* and adds an accountability story no detection-only demo will have.

## 7. COP frontend

- Dark MapLibre map, tracks with class glyph + velocity vector + **disposition ring**: green (FIRING-QUALITY), grey (CLEAR-HOLD), **amber pulsing (ESCALATE)**, with a confidence number.
- **Disposition card** (click a track): three gate scores as a traffic-light row, the top evidence factors per gate, the competing intent hypotheses with probabilities, the LLM's plain-language rationale, and the ROE/feasibility line.
- **Human-in-the-loop control**: every `ESCALATE` raises a card with `APPROVE / HOLD / REQUEST-MORE`; the decision and the operator id are logged.
- Side panel: decision-log tail + current chain root + per-track time-to-asset.

## 8. Adversarial demo scenarios (the red team)

| Injected case | Correct verdict | Why it's hard |
|---|---|---|
| Airliner on a filed airway, squawk 7700 (emergency) | `CLEAR-HOLD` autonomous | Looks like an unknown inbound until identity+intent resolve it |
| Ballistic inbound, supersonic, closing on asset, no IFF | `FIRING-QUALITY` autonomous | The clear-cut case the system should *not* waste a human on |
| **Subsonic, non-maneuvering, radar-silent, from neutral territory** | **`ESCALATE`** with hypotheses | The challenge's own scenario — genuinely ambiguous, must go to a human fast |
| Enemy aircraft **spoofing a civilian squawk / callsign** | `ESCALATE`, flag identity conflict | Registry says civilian, but route/kinematics don't match the claimed flight |
| Saturation: 12 ambiguous tracks at once | auto-triage: clear the clear, rank the rest | Shows the system buys the operator time under load |

## 9. Demo script (4 min)

1. **(30 s)** Problem: detection is solved; *judgment* is the bottleneck — a human can't validate every blip fast enough, and "approve everything" or "trust the AI" are both wrong.
2. **(60 s)** Live picture: real ADS-B over Paris + scripted military tracks. Click a civilian → auto `CLEAR-HOLD`, card shows the flight-DB match + 7700 squawk. Click the supersonic inbound → auto `FIRING-QUALITY`, card shows geometry + no civilian correlation + ROE pass.
3. **(75 s)** **Money shot — the neutral-territory unknown.** It appears, the system marks it `ESCALATE` in ~2 seconds, raises a commander card listing: *hypothesis A neutral transport (evidence…), hypothesis B masked threat (evidence…), hypothesis C nav error (evidence…)*, with confidences and the recommended next action. Narrate: *"Diplomatic channels take ten minutes. We don't decide for the commander on this one — we hand them the case, already built, in two seconds."*
4. **(45 s)** Spoofed-civilian: enemy squawks an airliner code → identity gate flags route/kinematic conflict → `ESCALATE` not `CLEAR-HOLD`. *"Detection-only systems take the squawk at face value. We cross-examine it."*
5. **(30 s)** Accountability: show the hash-chained decision log + checkpoint; prove an autonomous decision's reasoning is immutably recorded.
6. **(20 s)** Pathway: real radar/IFF integration, Mode 5, learned intent models, coalition registries.

## 10. Build plan (Fri 18:00 → Sun 12:00, team of 3–4)

- **Fri night:** track schema frozen; ADS-B ingest + scenario YAML; map renders tracks; registry stubs.
- **Sat AM:** three gates (deterministic) producing scores; disposition logic; disposition cards in COP.
- **Sat PM:** LLM reasoning layer (hypotheses + explanation) over gate evidence; hash-chained decision log; red-team scenarios incl. the neutral-territory case + spoofed civilian.
- **Sat night:** integration, full end-to-end run, freeze at midnight.
- **Sun AM:** rehearse ×3 on the scripted scenario; pitch deck (gate model, accountability, pathway).

**Hard rule:** anything not demoable by Saturday midnight becomes a slide.

## 11. Risks & fallbacks

| Risk | Fallback |
|---|---|
| Live ADS-B unavailable at venue | Pre-recorded OpenSky capture, replayed from disk |
| LLM latency/hallucination in the loop | LLM is advisory-only and off the firing path; cache its output per scenario for the rehearsed demo |
| Gate tuning misfires (wrong escalate/clear) | Tune against the deterministic scenario Sat night; demo runs the rehearsed scenario |
| "Where's the kinetic part?" from judges | Lean in: the *judgment layer* is the product; fire control is downstream and a non-goal — that's the honest, defensible scope |

## 12. What makes this win

Most teams will demo *detection* or a *dashboard*. Challenge #3 explicitly says detection is the solved-ish part and asks for **reasoning, confidence, human-in-the-loop, and explanation**. ARGUS demos a system that **autonomously clears the obvious, escalates the genuinely ambiguous with the case already argued, refuses to be fooled by a spoofed civilian squawk, and records every decision immutably** — answering the expert judge's hardest question ("how do you keep a human in control without being too slow?") not with a slide but with a live, adversarial demo.

## 13. Requirement mapping (put this in the deck)

| Challenge #3 asks for | ARGUS/VERDICT delivers |
|---|---|
| Validate target identity (friendly/neutral registries) | **Identity gate** — flight DB + friendly registry + IFF/transponder correlation |
| Assess intent (trajectory, behavior, emissions) | **Intent gate** — closing geometry, kinematic profile, emissions, route deviation |
| Evaluate feasibility (geometry, collateral, ammo, legal) | **Feasibility gate** — intercept envelope, collateral/airspace risk, resources, ROE geofence |
| Assign confidence, flag ambiguous for human review | Per-gate confidence + `ESCALATE` disposition with pre-built case |
| Keep humans in loop only when it truly matters | Autonomous `CLEAR-HOLD` / `FIRING-QUALITY` for the obvious; `ESCALATE` only for ambiguous/high-consequence |
| Explain reasoning to commanders | LLM rationale + per-gate factor list + **immutable hash-chained decision log** |
