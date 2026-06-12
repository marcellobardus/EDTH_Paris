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
