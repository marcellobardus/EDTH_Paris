// Implémentation réelle — branchée quand le backend FastAPI est prêt.
// Les endpoints correspondent aux dataclasses de contracts/contracts/messages.py.
// Le proxy Vite (/api → localhost:8000) est configuré dans vite.config.ts.

import type {
  Track, ThreatAssessment, Assignment, Interceptor,
  EngagementEvent, ControlResult,
} from './types'

const BASE = '/api'

async function _get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`)
  return r.json() as Promise<T>
}

async function _post<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { method: 'POST' })
  if (!r.ok) throw new Error(`POST ${path} → ${r.status}`)
  return r.json() as Promise<T>
}

export const getTracks = () => _get<Track[]>('/tracks')
export const getThreats = () => _get<ThreatAssessment[]>('/threats')
export const getAssignments = () => _get<Assignment[]>('/assignments')
export const getEngagementEvents = () => _get<EngagementEvent[]>('/engagement-events')
export const getInterceptorState = (id: string) =>
  _get<Interceptor | null>(`/interceptors/${id}/state`)

export const startSim = () => _post<ControlResult>('/sim/start')
export const stopSim = () => _post<ControlResult>('/sim/stop')
export const resetSim = () => _post<ControlResult>('/sim/reset')
