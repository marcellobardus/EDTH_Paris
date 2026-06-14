// Bridge — relie la couche API (mock ou backend) au dashboard standalone via son
// interface externe (window.__COP_setData / __COP_onControl).
//
// Flux :
//   1. polling api.ts (~5 Hz)  → tracks / threats / assignments / interceptors / events
//   2. agrégation en CopState → window.__COP_setData(state) → le dashboard redessine
//   3. boutons Run/Reset du dock → window.__COP_onControl → startSim/stopSim/resetSim
//
// Les positions sont déjà la ground-truth physique : le driver lit les poses gz et
// les republie en /simulation/ground_truth, dont l'agent dérive son InterceptorState.
// Le dashboard est donc synchro avec gz via le REST (pas besoin d'un lien gz direct).

import {
  getScenario, getTracks, getThreats, getAssignments,
  getInterceptorState, getEngagementEvents,
  startSim, stopSim, resetSim,
} from './api'

import type { CopState, Interceptor } from './types'

const POLL_MS = 200
const INTERCEPTOR_IDS = ['i1', 'i2', 'i3']

let _pollTimer: ReturnType<typeof setInterval> | null = null

// ── Boucle de polling : agrège un snapshot et l'injecte dans le dashboard ────────
async function poll(): Promise<void> {
  try {
    const [tracks, threats, assignments, events] = await Promise.all([
      getTracks(),
      getThreats(),
      getAssignments(),
      getEngagementEvents(),
    ])

    const interceptors = (
      await Promise.all(INTERCEPTOR_IDS.map(id => getInterceptorState(id)))
    ).filter((x): x is Interceptor => x != null)

    const state: CopState = { tracks, threats, interceptors, assignments, events }
    window.__COP_setData?.(state)
  } catch (err) {
    console.warn('[bridge] poll error:', err)
  }
}

// ── Démarrage ────────────────────────────────────────────────────────────────────
export function startBridge(): void {
  // Boutons du dock (Run / Reset) → appels backend via la couche API
  window.__COP_onControl = {
    start: () => { void startSim() },
    stop: () => { void stopSim() },
    reset: () => { void resetSim() },
  }

  // Géométrie statique (site défendu, ground station, radars) — chargée une fois
  // depuis la config sim et dessinée sur la carte. Sans bloquer le polling temps réel.
  void Promise.resolve(getScenario())
    .then(geo => { window.__COP_setScenario?.(geo) })
    .catch(err => console.warn('[bridge] scenario load failed:', err))

  // Polling de la couche API
  _pollTimer = setInterval(poll, POLL_MS)
  void poll()

  console.info('[bridge] started — external data feeding the dashboard')
}

export function stopBridge(): void {
  if (_pollTimer) clearInterval(_pollTimer)
  _pollTimer = null
}
