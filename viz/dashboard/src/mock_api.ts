// Mock fidèle des topics ROS2 définis dans contracts/contracts/messages.py.
// Simule le flux de données à ~5 Hz sans backend réel.
// Produit exactement le schéma consommé par le dashboard (voir types.ts).

import type {
  Track, ThreatAssessment, Assignment, Interceptor,
  EngagementEvent, ControlResult, ScenarioGeometry,
} from './types'

// ── Config scénario (mirrors scenario_default.yaml) ────────────────────────────
const TARGET: [number, number, number] = [500, 500, 0]
const INTERCEPTOR_IDS = ['i1', 'i2', 'i3']
const SHAHED_COUNT = 4
// Ground Station : poste de lancement au sol, FIXE. Les intercepteurs en décollent.
// (scenario_default.yaml → interceptors.launch_position)
const GROUND_STATION: [number, number, number] = [480, 480, 0]
const TICK_MS = 200                 // cadence de simulation interne
const ENGAGEMENT_INTERVAL = 15000   // ms entre kill/miss aléatoires

interface ShahedState {
  id: string
  pos: [number, number, number]
  vel: [number, number, number]
  alive: boolean
  threatScore: number
}

interface IntcState {
  id: string
  pos: [number, number, number]
  vel: [number, number, number]
  assignedTrack: string | null
  alive: boolean
}

// ── État interne de la simulation ───────────────────────────────────────────────
let _running = false
let _t = 0
let _tickTimer: ReturnType<typeof setInterval> | null = null
let _engagementTimer: ReturnType<typeof setTimeout> | null = null

const _shaheds: ShahedState[] = Array.from({ length: SHAHED_COUNT }, (_, i) => {
  const angle = (2 * Math.PI * i) / SHAHED_COUNT + Math.random() * 0.5
  const radius = 900 + Math.random() * 200
  const speed = 15 + Math.random() * 10
  return {
    id: `t${i + 1}`,
    pos: [
      TARGET[0] + Math.cos(angle) * radius,
      TARGET[1] + Math.sin(angle) * radius,
      50 + Math.random() * 100,
    ],
    vel: [
      -Math.cos(angle) * speed + (Math.random() - 0.5) * 3,
      -Math.sin(angle) * speed + (Math.random() - 0.5) * 3,
      0,
    ],
    alive: true,
    threatScore: 0.5 + Math.random() * 0.5,
  }
})

// Tous les intercepteurs décollent EXACTEMENT de la Ground Station (point fixe).
const _interceptors: IntcState[] = INTERCEPTOR_IDS.map((id, i) => ({
  id,
  pos: [...GROUND_STATION],
  vel: [0, 0, 0],
  assignedTrack: _shaheds[i]?.id ?? null,
  alive: true,
}))

// Assignments initiaux (Hungarian-like : i1→t1, i2→t2, i3→t3)
const _assignments = INTERCEPTOR_IDS.map((id, i) => ({
  interceptor_id: id,
  track_id: _shaheds[i]?.id ?? null,
}))

const _engagementEvents: EngagementEvent[] = []

// ── Tick de simulation ──────────────────────────────────────────────────────────
function _tick(): void {
  if (!_running) return
  _t += TICK_MS / 1000

  // Déplacer les Shaheds vers la cible
  for (const s of _shaheds) {
    if (!s.alive) continue
    const noise = () => (Math.random() - 0.5) * 2
    s.pos = [
      s.pos[0] + s.vel[0] * (TICK_MS / 1000) + noise(),
      s.pos[1] + s.vel[1] * (TICK_MS / 1000) + noise(),
      s.pos[2] + s.vel[2] * (TICK_MS / 1000),
    ]
  }

  // Déplacer les intercepteurs vers leur cible assignée (PN simplifié)
  for (const intr of _interceptors) {
    if (!intr.alive || !intr.assignedTrack) continue
    const tgt = _shaheds.find(s => s.id === intr.assignedTrack)
    if (!tgt || !tgt.alive) continue
    const dx = tgt.pos[0] - intr.pos[0]
    const dy = tgt.pos[1] - intr.pos[1]
    const dz = tgt.pos[2] - intr.pos[2]
    const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1
    const speed = 40
    intr.vel = [(dx / d) * speed, (dy / d) * speed, (dz / d) * speed]
    intr.pos = [
      intr.pos[0] + intr.vel[0] * (TICK_MS / 1000),
      intr.pos[1] + intr.vel[1] * (TICK_MS / 1000),
      intr.pos[2] + intr.vel[2] * (TICK_MS / 1000),
    ]
  }
}

function _scheduleEngagement(): void {
  _engagementTimer = setTimeout(() => {
    if (!_running) return
    const alive = _shaheds.filter(s => s.alive)
    if (alive.length > 0) {
      const target = alive[Math.floor(Math.random() * alive.length)]
      const intr = _interceptors.find(i => i.assignedTrack === target.id) ?? _interceptors[0]
      const success = Math.random() > 0.2
      if (success) target.alive = false
      _engagementEvents.push({
        interceptor_id: intr.id,
        track_id: target.id,
        success,
        timestamp: _t,
      })
      // Réassigner l'intercepteur si kill
      if (success) {
        const freeTarget = _shaheds.find(
          s => s.alive && !_interceptors.some(i => i.assignedTrack === s.id),
        )
        intr.assignedTrack = freeTarget?.id ?? null
      }
    }
    _scheduleEngagement()
  }, ENGAGEMENT_INTERVAL + Math.random() * 5000)
}

// Radars : positions fixes (mirror scenario_default.yaml → radars[]).
const RADARS: ScenarioGeometry['radars'] = [
  { radar_id: 'r1', position: [200, 500, 10], range: 800, fov_deg: 360 },
  { radar_id: 'r2', position: [800, 500, 10], range: 800, fov_deg: 360 },
]

// ── API publique (DataSource) ────────────────────────────────────────────────────

export function getScenario(): ScenarioGeometry {
  return {
    defended_site: [...TARGET] as [number, number, number],
    ground_station: [...GROUND_STATION] as [number, number, number],
    radars: RADARS.map(r => ({ ...r, position: [...r.position] as [number, number, number] })),
  }
}

export function getTracks(): Track[] {
  return _shaheds.map(s => ({
    track_id: s.id,
    position: [...s.pos] as [number, number, number],
    velocity: [...s.vel] as [number, number, number],
    alive: s.alive,
    timestamp: _t,
  }))
}

export function getThreats(): ThreatAssessment[] {
  return _shaheds
    .filter(s => s.alive)
    .map(s => {
      const dx = TARGET[0] - s.pos[0]
      const dy = TARGET[1] - s.pos[1]
      const d = Math.sqrt(dx * dx + dy * dy)
      const speed = Math.sqrt(s.vel[0] ** 2 + s.vel[1] ** 2) || 1
      return {
        track_id: s.id,
        severity: s.threatScore,
        eta_seconds: d / speed,
        timestamp: _t,
      }
    })
    .sort((a, b) => a.eta_seconds - b.eta_seconds)
}

export function getAssignments(): Assignment[] {
  return _assignments.filter(a => a.track_id !== null)
}

export function getInterceptorState(id: string): Interceptor | null {
  const intr = _interceptors.find(i => i.id === id)
  if (!intr) return null
  return {
    interceptor_id: intr.id,
    position: [...intr.pos] as [number, number, number],
    target_track_id: intr.assignedTrack,
    status: intr.assignedTrack ? 'ENGAGING' : 'READY',
    alive: intr.alive,
  }
}

export function getEngagementEvents(): EngagementEvent[] {
  return [..._engagementEvents]
}

export function startSim(): ControlResult {
  if (_running) return { ok: false, reason: 'already running' }
  _running = true
  _tickTimer = setInterval(_tick, TICK_MS)
  _scheduleEngagement()
  console.info('[mock] Simulation started')
  return { ok: true }
}

export function stopSim(): ControlResult {
  if (!_running) return { ok: false, reason: 'not running' }
  _running = false
  if (_tickTimer) clearInterval(_tickTimer)
  if (_engagementTimer) clearTimeout(_engagementTimer)
  console.info('[mock] Simulation stopped')
  return { ok: true }
}

export function resetSim(): ControlResult {
  stopSim()
  _t = 0
  _engagementEvents.length = 0
  for (const s of _shaheds) s.alive = true
  for (const intr of _interceptors) {
    intr.assignedTrack = _assignments.find(a => a.interceptor_id === intr.id)?.track_id ?? null
    intr.pos = [...GROUND_STATION]   // retour à la Ground Station
    intr.vel = [0, 0, 0]
  }
  console.info('[mock] Simulation reset')
  return { ok: true }
}
