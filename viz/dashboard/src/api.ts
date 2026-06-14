// Couche d'abstraction API — ne jamais appeler fetch directement depuis le bridge.
// Le contrat (types.ts) et le reste du frontend restent identiques quelle que soit
// la source. Sélection de la source, par ordre de priorité :
//   1. VITE_USE_MOCK=false (build)            → vrai backend FastAPI (viz.bridge)
//   2. ?real / ?mock dans l'URL               → bascule à chaud sans rebuild
//   3. défaut                                 → mock in-browser (dev autonome)

import type { DataSource } from './types'
import * as mock from './mock_api'
import * as real from './real_api'

function useMock(): boolean {
  const env = (import.meta as { env?: Record<string, string> }).env?.VITE_USE_MOCK
  if (env === 'false' || env === '0') return false
  if (env === 'true' || env === '1') return true
  if (typeof window !== 'undefined') {
    const q = new URLSearchParams(window.location.search)
    if (q.has('real')) return false
    if (q.has('mock')) return true
  }
  return true
}

const source: DataSource = useMock() ? mock : real

export const {
  getScenario,
  getTracks,
  getThreats,
  getAssignments,
  getInterceptorState,
  getEngagementEvents,
  startSim,
  stopSim,
  resetSim,
} = source
