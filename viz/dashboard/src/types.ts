// Types du contrat de données, alignés sur contracts/contracts/messages.py
// et sur le schéma consommé par window.__COP_setData (voir applyExternal() du HTML).
// Toutes les positions sont en mètres (x, y, z) ; le site défendu est à [500, 500, 0].

export type Vec3 = [number, number, number]

/** /gs/tracks — sortie du fusionneur Kalman. */
export interface Track {
  track_id: string
  position: Vec3
  velocity: Vec3
  eta_seconds?: number
  alive?: boolean
  timestamp?: number
}

/** /gs/threats — score de menace + ETA. */
export interface ThreatAssessment {
  track_id: string
  severity: number
  eta_seconds: number
  timestamp?: number
}

/** Statuts reconnus par mapStatus() côté HTML. */
export type InterceptorStatus =
  | 'READY'
  | 'ENGAGING'
  | 'RELOADING'
  | 'WINCHESTER'
  | 'DESTROYED'
  | string

/** /interceptors/{id}/state — état temps réel d'un intercepteur. */
export interface Interceptor {
  interceptor_id: string
  position: Vec3
  velocity?: Vec3
  status?: InterceptorStatus
  target_track_id?: string | null
  alive?: boolean
  // Champs optionnels d'enrichissement reconnus par applyExternal()
  name?: string
  range?: number
  ammo?: number
  ammo_max?: number
}

/** /gs/assignments — appariement intercepteur ↔ menace (fait autorité). */
export interface Assignment {
  interceptor_id: string
  track_id: string | null
}

/** /simulation/engagement — résultat d'un tir (kill/miss). */
export interface EngagementEvent {
  timestamp: number
  interceptor_id: string
  track_id: string
  success: boolean
}

/** Snapshot complet injecté dans le dashboard via window.__COP_setData. */
export interface CopState {
  tracks: Track[]
  threats: ThreatAssessment[]
  interceptors: Interceptor[]
  assignments: Assignment[]
  events: EngagementEvent[]
}

/** Un radar du scénario — position fixe + portée (anneau de couverture). */
export interface RadarSite {
  radar_id: string
  position: Vec3
  range: number
  fov_deg?: number
}

/** Géométrie statique de la carte (issue de la config sim — la source de vérité). */
export interface ScenarioGeometry {
  defended_site: Vec3       // site protégé (centre de la carte)
  ground_station: Vec3      // poste de lancement des intercepteurs
  radars: RadarSite[]
}

/** Résultat des appels de contrôle (start/stop/reset). */
export interface ControlResult {
  ok: boolean
  reason?: string
}

/** Surface commune que mock_api et real_api doivent exposer. */
export interface DataSource {
  getScenario(): ScenarioGeometry | Promise<ScenarioGeometry>
  getTracks(): Track[] | Promise<Track[]>
  getThreats(): ThreatAssessment[] | Promise<ThreatAssessment[]>
  getAssignments(): Assignment[] | Promise<Assignment[]>
  getInterceptorState(id: string): Interceptor | null | Promise<Interceptor | null>
  getEngagementEvents(): EngagementEvent[] | Promise<EngagementEvent[]>
  startSim(): ControlResult | Promise<ControlResult>
  stopSim(): ControlResult | Promise<ControlResult>
  resetSim(): ControlResult | Promise<ControlResult>
}

// ── Globals exposés par le dashboard standalone (Interceptor Mind) ──────────────
declare global {
  interface Window {
    __COP_EXTERNAL?: boolean
    __COP_STATE?: CopState
    __COP_setData?: (state: CopState) => CopState
    __COP_setScenario?: (geo: ScenarioGeometry) => void
    __COP_onControl?: {
      start?: () => void
      stop?: () => void
      reset?: () => void
    }
  }
}
