// Bridge — relie la couche API (mock ou backend) + le WebSocket Gazebo au
// dashboard standalone via son interface externe (window.__COP_setData / __COP_onControl).
//
// Flux :
//   1. polling api.ts (~5 Hz)  → tracks / threats / assignments / interceptors / events
//   2. WebSocket Gazebo (si connecté) → positions ground-truth qui priment sur l'API
//   3. agrégation en CopState → window.__COP_setData(state) → le dashboard redessine
//   4. boutons Run/Reset du dock → window.__COP_onControl → startSim/stopSim/resetSim

import {
  getTracks, getThreats, getAssignments,
  getInterceptorState, getEngagementEvents,
  startSim, stopSim, resetSim,
} from './api'

import {
  connectGazebo, onGroundTruth, onStatus,
  type GroundTruth,
} from './ws_gazebo'

import type { CopState, Track, Interceptor } from './types'

const POLL_MS = 200
const INTERCEPTOR_IDS = ['i1', 'i2', 'i3']

let _pollTimer: ReturnType<typeof setInterval> | null = null
let _latestGroundTruth: GroundTruth | null = null

// Normalise un nom de modèle Gazebo vers l'id utilisé côté API/mock.
//   interceptor_1 → i1   |   shahed_1 / drone_1 → t1   |   t1 → t1 (inchangé)
function normalizeId(name: string): string {
  const m = name.match(/(\d+)$/)
  const n = m ? m[1] : ''
  if (/^interceptor/i.test(name)) return `i${n}`
  if (/^(shahed|drone|threat)/i.test(name)) return `t${n}`
  return name
}

// ── Fusion : positions Gazebo prioritaires sur celles de l'API ──────────────────
function applyGroundTruth(tracks: Track[], interceptors: Interceptor[]): void {
  const gt = _latestGroundTruth
  if (!gt) return
  // tolérance de fraîcheur : on ignore un ground-truth de plus de 2 s
  if (Date.now() / 1000 - gt.timestamp > 2) return

  for (const obj of gt.objects) {
    const id = normalizeId(obj.object_id)
    if (obj.kind === 'shahed') {
      const t = tracks.find(x => x.track_id === id)
      if (t) t.position = obj.position
    } else {
      const intr = interceptors.find(x => x.interceptor_id === id)
      if (intr) intr.position = obj.position
    }
  }
}

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

    applyGroundTruth(tracks, interceptors)

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

  // WebSocket Gazebo : positions temps réel + LED de connexion (gérée par le HTML)
  onStatus((connected) => {
    console.info(`[bridge] Gazebo WS ${connected ? 'connected' : 'disconnected'}`)
  })
  onGroundTruth((gt) => { _latestGroundTruth = gt })
  connectGazebo()

  // Polling de la couche API
  _pollTimer = setInterval(poll, POLL_MS)
  void poll()

  console.info('[bridge] started — external data feeding the dashboard')
}

export function stopBridge(): void {
  if (_pollTimer) clearInterval(_pollTimer)
  _pollTimer = null
}
