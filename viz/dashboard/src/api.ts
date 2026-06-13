// Couche d'abstraction API — ne jamais appeler fetch directement depuis le bridge.
// Pour basculer sur le vrai backend : passer USE_MOCK à false. C'est la SEULE ligne
// à changer ; le contrat (types.ts) et le reste du frontend restent identiques.

import type { DataSource } from './types'
import * as mock from './mock_api'
import * as real from './real_api'

const USE_MOCK = true

const source: DataSource = USE_MOCK ? mock : real

export const {
  getTracks,
  getThreats,
  getAssignments,
  getInterceptorState,
  getEngagementEvents,
  startSim,
  stopSim,
  resetSim,
} = source
