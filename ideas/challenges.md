# EDTH Paris — Hackathon Challenges

**Venue:** 5 Rue La Boétie, 75008 Paris, France
**Dates:** June 12–14, 2026

---

## 1. Detect objects on data feeds that are close to invisible to the human eye

**Source:** Alta Ares
**Mentors:** Valentin Grateau, Jean-Max Rodriguez, Félix Courtin

**Problem statement:**
Design a multi-modal detection system that spots what operators miss: vehicles in shadows, personnel under foliage, camouflaged positions, across RGB, infrared, and other sensor feeds. Fuse multiple spectrums into a single operational picture that detects threats faster and more reliably than any individual sensor or human eye alone.

**Context:**
In ISR missions, operators monitor multiple live feeds trying to spot threats in real time. Single-modality vision models are widely used and help automate this, but they only work on obvious, well-contrasted objects, not the hard cases that matter most: vehicles tucked in treelines, personnel in deep shadow, camouflaged positions washed out by light conditions.

The challenge is to push detection into this "near-invisible" territory by combining sensor modalities (RGB, infrared, audio, RF) as this is where vision models truly stand out compared to the human eye.

A Raspberry Pi 5 with RGB camera, IR camera, and microphones will be available on-site. Making your solution run on this edge hardware is a strong plus — it reflects tactical reality where compute is limited and latency matters.

Open-source datasets to get started: VisDrone (DroneVehicle), multimodal datasets.

**Operational Scenario:**
An infantry section deploys to a forward position in eastern Ukraine along a contested treeline. Standard first move: the reconnaissance drone goes up to scan for hidden threats before the unit advances: dug-in vehicles, concealed observation posts, personnel under canopy.

The operator streams live RGB and IR feeds back to a tablet. The terrain is cluttered: dense vegetation, burnt-out structures, deep afternoon shadows. Dozens of hectares to scan, two feeds to watch, and every ambiguous signature — camouflaged armor under netting, a prone observer in a bush line — looks like terrain noise. The section cannot move until the area is cleared, but the operator is saturated. The threats standard models miss on clean imagery are exactly the ones that will kill. The unit needs a system that fuses what the drone sees across spectrums and flags what the human eye cannot catch before the section steps off.

---

## 2. Real-Time Multi-Interceptor Coordination and Threat Assignment

**Source:** Alta Ares
**Mentors:** Valentin Grateau, Jean-Max Rodriguez, Félix Courtin

**Problem statement:**
Modern threats come from every direction, simultaneous attacks exceed single-interceptor capacity. Build systems that coordinate multiple interceptors, fuse their distributed sensors, and assign incoming threats in real time with optimal targeting to neutralize coordinated and saturation attacks before they reach defended assets.

**Context:**
Air defense requires rapid response to multiple simultaneous threats. Current systems handle threats sequentially or with manual coordination, creating dangerous gaps. A networked defense needs:

- **Distributed sensor fusion:** Combine radar, optical, and RF data from multiple interceptor platforms
- **Real-time threat assignment:** Optimize allocation of limited interceptors to maximize threats neutralized
- **Coordination algorithms:** Share targeting data across interceptor network with minimal latency
- **Dynamic re-tasking:** Reassign interceptors mid-engagement if threat priorities change

**Methods:** Graph-based optimization (Hungarian algorithm, max-flow), network protocols (publish-subscribe, edge computing), Kalman filtering for track fusion, game theory for competitive threat assignment, consensus algorithms.

**Operational Scenario:**
A forward defense site faces a coordinated attack: 4 simultaneous drone threats approaching from different vectors, combined with decoy swarms. The site has 3 interceptor systems (each with limited ammo and engagement range). Current manual coordination takes 15–20 seconds per engagement decision, too slow for saturation attacks.

Build a real-time system that:
- Fuses radar and optical sensors from all 3 interceptor platforms into a unified track picture
- Automatically prioritizes threats (by speed, proximity, danger assessment)
- Assigns each interceptor optimal targets based on range, reload time, and engagement probability
- Tracks ammunition availability and interceptor state across the network
- Recomputes assignments every 1–2 seconds as threats move
- Outputs firing recommendations with confidence scores for each

---

## 3. Target Validation and Intent Assessment for Autonomous Engagement Decisions

**Source:** Alta Ares
**Mentors:** Valentin Grateau, Jean-Max Rodriguez, Félix Courtin

**Problem statement:**
Detection alone is not enough — a radar blip could be friend, foe, or civilian. Build reasoning systems that validate targets, assess intent, and evaluate engagement feasibility in real time, enabling autonomous decisions that keep humans in the loop only when it truly matters: high-stakes, ambiguous, or time-critical moments.

**Context:**
Current engagement rules require human approval for every shot, necessary for legal/ethical reasons, but too slow for supersonic threats or saturation attacks. The solution: autonomous reasoning that handles routine, low-ambiguity cases instantly while escalating uncertain or high-consequence decisions to humans. A reasoning engine must:

- **Validate target identity:** Is this actually a threat? Check against friendly/neutral registries
- **Assess intent:** Is this target hostile or non-threatening? Analyze trajectory, behavior, emissions
- **Evaluate feasibility:** Can we engage this target? Check intercept geometry, collateral risk, ammunition
- **Assign confidence:** How certain is the decision? Flag ambiguous cases for human review
- **Explain reasoning:** Show commanders the factors driving autonomous decisions

**Operational Scenario:**
A defense site detects an unknown aircraft approaching from neutral territory. Radar shows it's subsonic, non-maneuvering, no active radar emissions. Is it a civilian airliner, a neutral military transport, or a threat? Standard identification takes 10+ minutes via diplomatic channels, too slow if intent is hostile.

Build a reasoning system that:
- Correlates the radar track with known flight databases (civilian, friendly military)
- Analyzes behavior: altitude changes, speed profile, transponder status, radio emissions
- Assesses threat probability: hostile intent vs. navigation error vs. civilian emergency
- Evaluates engagement feasibility: intercept geometry, collateral damage risk, legal basis

---

## 4. Interception of drones

**Source:** ARTEFACT

**Problem statement:**
Design and prototype an innovative, software-based detection and tracking system that protects sensitive sites and deployed forces from the proliferation of low-altitude, low-speed drones and remotely operated munitions (ROM).

The solution must overcome the limitations of conventional radar systems when dealing with very low radar cross-sections and specific flight characteristics. Specifically, the system must process and synthesize sensor or tracking data to provide accurate, real-time tracking.

**Context:**
In a context of significant proliferation of drones and remotely operated munitions (ROM), the protection of sensitive sites and deployed forces requires new detection and tracking capabilities. Conventional radar solutions are now reaching their limits when faced with the very low signature of these objects and their flight characteristics. Since physical test flights are restricted within the Paris office venue, this challenge focuses on software-driven innovation, data fusion, and virtual tracking systems to counter low-altitude threats.

**Operational Scenario:**
During a training exercise in a military camp, several small, low-speed reconnaissance drones are deployed by a simulated adversary to gather intelligence. Because these drones fly at a very low altitude and have an extremely small signature, conventional radars fail to detect them. The training center lacks the capability to see or track these threats in real time.

How the solution would be used: Tactical operators on-site deploy the software solution, which connects to a network of low-cost passive sensors. The software instantly processes the data to isolate the low-altitude targets. It displays the drones' exact speed and 3D position in real time on a visualization screen. This allows the operators to safely log the flight data, track the threat continuously, and plan a simulated interception protocol without any risk to real-world airspace safety.

---

## 5. Voice-to-Map: Real-Time Positional Tracking

**Source:** Lysk

**Problem statement:**
Teams give location updates by voice, but manual map updates are slow and error-prone. Build a web app that turns spoken updates into automatic live map updates.

**Context:**
In dynamic operations, people naturally report positions like "I am behind the church" and movements like "I moved towards 35th street." The system should interpret this natural language and keep a shared map up to date.

**Suggested stack:** OpenStreetMap APIs for geocoding, Deepgram for speech-to-text, Mistral for intent extraction. Reach out to marek@lysk.ai for Deepgram API keys.

**Operational Scenario:**
A user speaks short status updates into the app. The app transcribes speech, extracts position/movement intent, resolves landmarks/streets, and updates a marker/path on the map in near real time, with timestamps and history.

---

## 6. Improve Speech-to-Text on Walkie-Talkie Audio

**Source:** Lysk

**Problem statement:**
Take a public push-to-talk / walkie-talkie speech dataset and beat off-the-shelf speech-to-text models on word error rate, through fine-tuning, adaptation, or pre-processing.

**Context:**
Modern STT models degrade badly on PTT radio audio: narrow bandwidth, compression artifacts, abrupt starts/ends, domain-specific vocabulary and callsigns. Public labelled radio-speech datasets exist (e.g. on Hugging Face or Zenodo) but are small, so data-efficient methods matter.

**Operational Scenario:**
A transcription service for radio traffic is upgraded with the improved model. Messages previously transcribed with garbled callsigns and missed keywords now produce accurate text, making downstream event extraction reliable.

---

## 7. Collaborative Jammer Detection and Localization for Contested Environments

**Source:** Durandal
**Mentors:** Vincent Lordier, Durandal

**Problem statement:**
Design and prototype a system capable of detecting, classifying, and geolocating hostile radio-frequency (RF) interference sources using multiple distributed sensors.

The solution should enable military units operating in GNSS- and communications-denied environments to rapidly identify the presence and approximate location of electronic warfare (EW) systems, enabling countermeasures, route planning, and improved operational awareness.

**Context:**
Electronic warfare has become a defining feature of modern conflicts. GPS jammers, command-link disruptors, and wideband interference systems are routinely employed to degrade drone operations, disrupt communications, and reduce battlefield awareness.

Frontline units often know that jamming is occurring but lack the tools to determine:
- whether the interference is accidental or hostile,
- what type of jammer is being used,
- where the jammer is located,
- whether the affected area can still support operations.

Current EW systems are often expensive, centralized, and inaccessible to small tactical units. Teams are encouraged to explore signal processing approaches.

**Operational Scenario:**
A reconnaissance platoon operating near a contested urban area experiences repeated GPS degradation and intermittent communication failures during drone missions. Command suspects the presence of mobile enemy jamming systems supporting offensive operations.

The unit deploys several portable sensing nodes across the area of operations. By collaboratively analyzing RF activity, the system detects anomalous signals, estimates the jammer's position, and visualizes the threat area for operators. The resulting information allows commanders to adjust drone flight paths and avoid heavily contested electromagnetic zones.

---

## 8. Self-Healing Autonomous Communications Network for Contested Environments

**Source:** Durandal
**Mentors:** Vincent Lordier, Durandal

**Problem statement:**
Design and prototype a resilient communications network capable of autonomously adapting to changing battlefield conditions, including node loss, jamming, spectrum congestion, and mobility.

The solution should maintain connectivity between users and autonomous systems despite adversarial interference and degraded infrastructure. Teams are encouraged to explore adaptive routing, spectrum awareness, mesh networking, autonomous reconfiguration, and distributed decision-making.

**Context:**
Reliable communications are essential for command and control, situational awareness, and coordination between human operators and autonomous systems. In modern conflicts, however, communications infrastructure is routinely targeted through electronic warfare or even kinetic attacks.

Traditional communications architectures often depend on static planning and centralized management, making them vulnerable in contested environments. Future tactical networks must be capable of:
- detecting degradation,
- rerouting traffic automatically,
- adapting transmission strategies,
- reorganizing themselves when nodes are lost,
- operating with limited operator intervention.

**Operational Scenario:**
A distributed force consisting of infantry units, reconnaissance teams, and unmanned systems is conducting operations in an area with persistent enemy electronic attack. Several communications nodes are destroyed, while others experience intermittent jamming.

Rather than relying on manual intervention, the network autonomously identifies disruptions, reconfigures routes, prioritizes critical traffic, and restores connectivity through alternative paths. Operators continue to exchange information despite ongoing interference, allowing the force to sustain coordination and mission effectiveness. The system should demonstrate how tactical communications can remain operational even when the environment is dynamic, degraded, and contested.
