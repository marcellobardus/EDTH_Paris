// Connexion directe au WebSocket Gazebo (port 9002, gz-sim-websocket-server-system).
// Fournit les positions temps réel "ground truth" des objets de la simulation.
// Reconnexion automatique avec backoff exponentiel.

const GZ_WS_URL = 'ws://localhost:9002'
const BACKOFF_INITIAL = 1000
const BACKOFF_MAX = 5000

export interface GroundTruthObject {
  object_id: string
  kind: 'interceptor' | 'shahed'
  position: [number, number, number]
  velocity: [number, number, number]
  alive: boolean
}

export interface GroundTruth {
  objects: GroundTruthObject[]
  timestamp: number
}

type GroundTruthCb = (gt: GroundTruth) => void
type StatusCb = (connected: boolean) => void

let _ws: WebSocket | null = null
let _backoff = BACKOFF_INITIAL
let _groundTruthCb: GroundTruthCb | null = null
let _statusCb: StatusCb | null = null

export function onGroundTruth(cb: GroundTruthCb): void { _groundTruthCb = cb }
export function onStatus(cb: StatusCb): void { _statusCb = cb }

function _setStatus(connected: boolean): void {
  _statusCb?.(connected)
}

interface GzPose {
  name?: string
  position?: { x?: number; y?: number; z?: number }
}

function _handleMessage(raw: string): void {
  let msg: any
  try { msg = JSON.parse(raw) } catch { return }

  // Gazebo encapsule les messages avec un champ "type"
  const type: string = msg?.type ?? msg?.msg_type ?? ''

  if (type.includes('Pose_V') || type.includes('pose_v')) {
    const poses: GzPose[] = msg.data?.pose ?? msg.pose ?? []
    const objects: GroundTruthObject[] = poses.map(p => {
      const name = p.name ?? ''
      const kind: GroundTruthObject['kind'] =
        name.startsWith('interceptor') || /^i\d/.test(name) ? 'interceptor' : 'shahed'
      return {
        object_id: name,
        kind,
        position: [p.position?.x ?? 0, p.position?.y ?? 0, p.position?.z ?? 0],
        velocity: [0, 0, 0],
        alive: true,
      }
    })
    _groundTruthCb?.({ objects, timestamp: Date.now() / 1000 })
  }
}

function connect(): void {
  if (_ws) return

  _ws = new WebSocket(GZ_WS_URL)

  _ws.onopen = () => {
    _backoff = BACKOFF_INITIAL
    _setStatus(true)
    console.info('[gz-ws] Connected to Gazebo WebSocket')
    _ws?.send(JSON.stringify({ op: 'subscribe', topic: '/world/world_demo/dynamic_pose/info' }))
    _ws?.send(JSON.stringify({ op: 'subscribe', topic: '/world/world_demo/pose/info' }))
  }

  _ws.onmessage = (e) => _handleMessage(e.data as string)

  _ws.onclose = () => {
    _ws = null
    _setStatus(false)
    console.warn(`[gz-ws] Disconnected — retry in ${_backoff}ms`)
    setTimeout(connect, _backoff)
    _backoff = Math.min(_backoff * 2, BACKOFF_MAX)
  }

  _ws.onerror = () => {
    // onclose s'exécutera ensuite — rien à faire ici
  }
}

export function connectGazebo(): void { connect() }

export function disconnectGazebo(): void {
  if (_ws) { _ws.onclose = null; _ws.close(); _ws = null }
  _setStatus(false)
}
