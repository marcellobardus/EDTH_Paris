# LEUKO — Distributed Immune Layer for Contested Networks

**Project spec — European Defense Tech Hackathon, Paris, June 12–14 2026**
**Challenge fit:** Durandal #7 — *Collaborative Jammer Detection and Localization* **and** Durandal #8 — *Self-Healing Autonomous Communications Network* (one substrate, two heads).
**Origin:** This idea is the dissent-accounting / node-reputation mechanism extracted out of ARGUS's Jury and made the *whole product*. Where ARGUS asks *"is this **target** real?"*, LEUKO asks *"is this **node** healthy — and if not, why, and what do we do about it?"*

---

## 1. One-sentence thesis

**A contested network's own pattern of degradation is a sensor.** Most systems treat node failure, jamming, and compromise as noise to be tolerated. LEUKO treats it as *signal*: a distributed anomaly layer continuously cross-examines every node against its peers, flags the outliers as **"infected"** with a *cause* (`JAMMED` / `COMPROMISED` / `FAULTY`), and routes that diagnosis to two outputs — **localize the attacker** (#7) and **heal the network** (#8). Same immune mechanism: diagnose the pathogen, isolate it, keep the body running.

**Pitch line:** *"When the enemy jams or captures part of your network, your network already knows — it just isn't listening to itself. LEUKO is the immune system: it detects its own infected cells, points at what infected them, and routes around them automatically."*

## 2. The core idea — outlier detection → an "infected" flag with a cause

Every node periodically emits a lightweight **health beacon**: its own state (per-band RF noise floor, packet-loss rate, GNSS lock quality, time-sync drift, neighbor RSSI) plus a few observations about its neighbors. A central (demo) or gossip-distributed (roadmap) **anomaly engine** maintains a model of "normal" per node and per neighborhood, and flags deviations. Crucially, the flag carries a **cause**, because the *signature* of the anomaly tells you what kind of infection it is:

| Signature | Cause | Meaning |
|---|---|---|
| **Spatially correlated** degradation — a contiguous cluster of nodes loses signal together, sharp onset, specific band(s), elevated noise floor | `JAMMED` | external RF attack — feeds the **localization head (#7)** |
| **Individually inconsistent** — one node's *data/behavior* contradicts what corroborating neighbors report, despite the node claiming health (malformed packets, asserts observations no neighbor supports, lies about its own state) | `COMPROMISED` | captured / spoofing node — feeds the **self-healing head (#8)** and protects #7 from poisoning |
| **Isolated, uncorrelated** drift (hardware degradation, battery, single-link loss) | `FAULTY` | benign failure — graceful degrade + maintenance flag |

This three-way diagnosis is the heart of LEUKO. The cause decides the response.

## 3. Two application heads (one engine)

```
                       ┌────────────────────────────┐
   health beacons ───► │   ANOMALY / IMMUNE ENGINE  │
   from all nodes      │  per-node + per-neighborhood│
                       │  baseline → outlier → CAUSE │
                       └───────────┬─────────────────┘
                      infected-node set + causes
                ┌──────────────────┴───────────────────┐
        ┌───────▼─────────┐                   ┌─────────▼────────┐
        │ LOCALIZATION head│  (Challenge #7)   │ SELF-HEALING head │ (Challenge #8)
        │ JAMMED cluster → │                   │ COMPROMISED →     │
        │ emitter position │                   │   quarantine      │
        │ + type + threat  │                   │ JAMMED dead-zone →│
        │   ring on map    │                   │   reroute around  │
        └──────────────────┘                   │ + prioritize crit │
                                               └───────────────────┘
```

### 3.1 Localization head → Challenge #7 (Collaborative Jammer Detection)
The set of `JAMMED` nodes *is* the jammer's footprint. From it:
- **Locate the emitter.** The boundary/gradient of the affected region points back to the source: nodes nearer the jammer show a higher noise floor / lower SNR. A power-difference (RSS-gradient) or AoA-boundary estimate over the `JAMMED` cluster gives an approximate emitter position; multiple boundary arcs ⇒ multilateration. (TDoA is roadmap — needs sync; the gradient/boundary method works with the data we already have.)
- **Classify the jammer.** *Which* bands/services went infected → GPS jammer (GNSS lock lost, RF clean elsewhere) vs. comms disruptor (specific comms band) vs. wideband (everything). 
- **Accidental vs. hostile.** A deliberate emitter produces a coherent, persistent, spatially-structured footprint; environmental/accidental interference is diffuse, intermittent, unstructured. The engine scores this.
- **Threat-area visualization.** The infected map *is* the "visualize the threat area for operators" deliverable the challenge asks for; commanders read the affected zone and the estimated emitter directly.

### 3.2 Self-healing head → Challenge #8 (Self-Healing Comms Network)
The infected-node set is the trigger and the map for autonomous reconfiguration:
- **Quarantine `COMPROMISED` nodes.** Exclude them from routing trust and from the data plane — a captured node is no longer believed or relied upon (this is literally ARGUS's captured-node defense, applied to the network fabric).
- **Route around `JAMMED` dead-zones.** Recompute paths over the *clean* subgraph; the mesh reorganizes when nodes are lost.
- **Prioritize critical traffic** onto the surviving high-quality links; degrade gracefully — always toward *less* trust / *fewer* assumptions, never toward false confidence.
- **Auto-recover.** When a node's health returns (jammer moves on, link restored), hysteresis clears the flag and it rejoins.

### 3.3 Why these two challenges are the same substrate
Both #7 and #8 begin with the identical hard problem: *figure out which parts of your distributed network are degraded and why.* #7 turns that into **localization** (point at the attacker); #8 turns it into **reconfiguration** (heal the body). LEUKO computes the diagnosis once and feeds both. That's the strategic bet: one core, two challenge-winning deliverables, and they reinforce each other (the self-healing layer protects the localization layer from poisoned data; the localization layer tells the self-healing layer *why* a zone is bad).

## 4. Architecture

| Component | Stack | Does |
|---|---|---|
| `node/` agent | Python (sim ×N) + optional ESP32 ×1–2 | emits health beacon: RF noise/band, packet-loss, GNSS quality, sync drift, neighbor RSSI; relays traffic in the mesh |
| `engine/` immune core | Python | per-node + per-neighborhood baselines; outlier detection; **cause classification** (JAMMED/COMPROMISED/FAULTY) |
| `locate/` head (#7) | Python | RSS-gradient / boundary multilateration over JAMMED cluster; band-based jammer classification; accidental-vs-hostile score |
| `heal/` head (#8) | Python | clean-subgraph routing, quarantine list, critical-traffic prioritization, recovery hysteresis |
| `cop/` frontend | React + MapLibre GL + deck.gl | node map color-coded by health/cause; estimated jammer + threat ring; live mesh topology + traffic flow re-routing on screen |
| `redteam/` injector | Python | drop a jammer (moveable), capture/compromise a node, kill nodes, congest spectrum |

Keep engine + both heads one process. Two processes total (backend, frontend) plus nodes.

## 5. The anomaly engine (how "infected" is decided)

Deliberately simple, demoable, honest:
- **Baseline:** per node, a rolling median + MAD (robust to a few bad samples) of each health metric; per neighborhood, the distribution across spatial neighbors.
- **Outlier test:** a node is anomalous when its metrics deviate > k·MAD from *both* its own baseline and its neighborhood — and the deviation persists past a debounce window (no flapping on a single bad sample).
- **Cause classification (the interesting part):**
  - count how many *spatially adjacent* nodes are anomalous in the *same band at the same time* → high spatial correlation ⇒ `JAMMED`.
  - check whether the node's *self-reported* health matches what neighbors *observe* about it, and whether its data is internally/geometrically consistent → mismatch ⇒ `COMPROMISED`.
  - anomalous but isolated and uncorrelated ⇒ `FAULTY`.
- **Reputation / strikes (inherited from ARGUS):** repeat `COMPROMISED` behavior accrues strikes → node muted (weight 0) until an operator clears it. This is how a captured node mutes *itself* with no revocation infrastructure.

Complexity is trivial (O(nodes·metrics) per tick) — the engine is never the bottleneck; the point is the *classification logic*, not heavy ML.

## 6. Demo (4 min)

A map of ~12–20 simulated nodes (plus 1–2 real ESP32 if time) in a mesh, traffic flowing.

1. **(30 s)** Problem: in EW-contested ops you know *something* is wrong but not *which nodes, why, or where the jammer is* — and your network keeps trusting dead/captured nodes.
2. **(70 s, #7)** Drop a **jammer** on the map. A cluster of nodes flips `JAMMED` (red), the rest stay green. LEUKO draws the **estimated jammer position + threat ring** and labels the type (GPS vs comms vs wideband). Move the jammer → the infected cluster moves → the estimate tracks it. *"The network's own pain map points straight back at the source."*
3. **(70 s, #8)** **Capture a node** (red-team makes it emit plausible-but-inconsistent data). LEUKO flags it `COMPROMISED`, quarantines it, and you watch traffic **re-route around it** live; critical traffic stays up. Then **kill two nodes** in a jammed zone → mesh **self-heals** over the clean subgraph on screen. Restore them → they rejoin.
4. **(30 s)** Honesty slide: what defeats it (k colluding captured nodes with consistent geometry; raise k / secure elements — roadmap), and that degradation is always toward *less* trust.
5. **(20 s)** Pathway: gossip-distributed engine (no central point), TDoA localization with synced nodes, secure elements, learned anomaly baselines.

## 7. Which challenge to *lead* with (you can't fully build both in a weekend)

Both heads share the engine, but for judging you submit to one challenge and present the other as "the same signal also does this."

- **Lead with #7 (Jammer Detection)** if you want the crisper, more self-contained, more visually obvious deliverable — *"here's the jammer, here's its type, here's the threat zone"* is instantly judgeable. Self-healing is the bonus.
- **Lead with #8 (Self-Healing)** if you want the more systems-impressive demo — live re-routing and quarantine looks like real infrastructure. Jammer-localization becomes "and the same infected map tells you where the attack is coming from."

**Recommendation:** build the engine + both heads, but **lead with #7** — localization is a tighter, lower-risk thing to get demo-solid by Saturday midnight, and the self-healing footage is a strong closer rather than a load-bearing requirement.

## 8. Relationship to ARGUS (shared scaffold)

LEUKO and ARGUS are sister projects with the same skeleton — distributed nodes, central engine, COP map, red-team injector, the reputation/strike logic — and the same philosophy: *the network cross-examines itself; a valid signature / a live node is not the same as a trustworthy one.* If the team builds one, ~60% of the other's infrastructure comes free. ARGUS's `COMPROMISED`-node detection **is** LEUKO's `COMPROMISED` head, generalized from "captured sensor lying about a target" to "captured node lying about anything." Pick based on which challenge slot you'd rather contest.

## 9. Risks & fallbacks

| Risk | Fallback |
|---|---|
| Jammer-localization accuracy looks weak | Demo as an *approximate threat ring*, not a pinpoint — the challenge asks for "approximate location"; honesty plays well |
| Compromised-vs-faulty classification misfires | Tune thresholds against the deterministic scenario Sat night; demo runs the rehearsed injection sequence |
| Self-healing re-routing looks janky | Fewer nodes, slower scenario, cleaner topology — legible beats realistic |
| "Is this just anomaly detection?" from judges | The differentiator is the **cause classification + dual output**: same anomaly → *localize the enemy* and *heal yourself*. Generic anomaly detection does neither |
| No real RF hardware | Simulate noise-floor/SNR per node from the scenario; 1–2 ESP32 reading real RSSI is a bonus, not required |

## 10. What makes this win

Other #7 teams will build an RF-localization point solution; other #8 teams will build a routing protocol. LEUKO reframes both as one idea — **the degradation pattern is intelligence** — and demos a network that *diagnoses its own infected nodes, names the cause, points at the jammer, and routes around the damage* in a single live, adversarial run. The immune-system framing is memorable, the dual-challenge coverage is unusual, the scope degrades gracefully, and the captured-node story is genuinely novel (it's ARGUS's hardest-to-counter attack, turned into a network-health primitive).

## 11. Requirement mapping (put this in the deck)

**Challenge #7 — Collaborative Jammer Detection:**

| Asks for | LEUKO delivers |
|---|---|
| Detect RF interference via multiple distributed sensors | Spatially-correlated `JAMMED` flags across the node mesh |
| Classify jammer type | Which bands/services went infected → GPS / comms / wideband |
| Geolocate the emitter | RSS-gradient / boundary multilateration over the JAMMED cluster |
| Accidental vs. hostile | Coherence/persistence/structure score of the infected footprint |
| Operate in GNSS/comms-denied env | Uses surveyed node positions + relative health; no reliance on the jammed services |
| Visualize threat area | The infected map + estimated emitter + threat ring on the COP |

**Challenge #8 — Self-Healing Comms Network:**

| Asks for | LEUKO delivers |
|---|---|
| Detect degradation | The anomaly engine, continuously |
| Reroute traffic automatically | `heal/` recomputes paths over the clean subgraph |
| Reorganize when nodes are lost | Quarantine + topology recompute on flag/kill |
| Adapt to jamming / congestion / mobility | Cause-aware response (route around JAMMED, quarantine COMPROMISED) |
| Limited operator intervention | Autonomous flag → quarantine → reroute → recover, with hysteresis |
| Prioritize critical traffic | Critical flows pinned to surviving high-quality links |
