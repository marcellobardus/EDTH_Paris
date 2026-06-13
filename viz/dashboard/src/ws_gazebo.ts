// Connexion au WebSocket Gazebo (port 9002) via la lib officielle `gzweb`.
//
// La sim lance un bridge `gz::launch::WebsocketServer` (protocole gz-transport
// WebSocket : handshake `auth`/`protos`, puis frames `pub,<topic>,<type>,<protobuf>`).
// La lib `gzweb` (Transport + Topic) gère tout ce protocole et décode le protobuf,
// nous livrant des messages JS prêts à l'emploi — on ne refait donc PAS le parsing.
//
// On souscrit aux poses dynamiques du monde et on en extrait les positions des
// modèles (interceptor_1/2/3, shaheds…) pour alimenter la carte 2D du dashboard.

import { Transport, Topic } from 'gzweb'

const GZ_WS_URL = 'ws://localhost:9002'
const WORLD = 'intercept_scenario'
const DYNAMIC_POSE_TOPIC = `/world/${WORLD}/dynamic_pose/info`
const POSE_TOPIC = `/world/${WORLD}/pose/info`

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

let _transport: Transport | null = null
let _groundTruthCb: GroundTruthCb | null = null
let _statusCb: StatusCb | null = null

// Dernières positions connues par modèle (les frames dynamic_pose ne contiennent
// que les modèles qui ont bougé ; on conserve un état cumulé).
const _positions = new Map<string, [number, number, number]>()

export function onGroundTruth(cb: GroundTruthCb): void { _groundTruthCb = cb }
export function onStatus(cb: StatusCb): void { _statusCb = cb }

function classify(name: string): GroundTruthObject['kind'] {
  return /^interceptor/i.test(name) || /^i\d/.test(name) ? 'interceptor' : 'shahed'
}

// Un Pose_V décodé ressemble à { pose: [{ name, position:{x,y,z}, orientation:{…} }, …] }
interface GzPose {
  name?: string
  position?: { x?: number; y?: number; z?: number }
}

function handlePoseV(msg: { pose?: GzPose[] }): void {
  const poses = msg?.pose ?? []
  for (const p of poses) {
    if (!p.name) continue
    _positions.set(p.name, [p.position?.x ?? 0, p.position?.y ?? 0, p.position?.z ?? 0])
  }

  const objects: GroundTruthObject[] = []
  for (const [name, position] of _positions) {
    objects.push({
      object_id: name,
      kind: classify(name),
      position,
      velocity: [0, 0, 0], // Gazebo ne publie pas la vélocité ici ; non requis pour la carte
      alive: true,
    })
  }
  _groundTruthCb?.({ objects, timestamp: Date.now() / 1000 })
}

export function connectGazebo(): void {
  if (_transport) return

  _transport = new Transport()

  _transport.getConnectionStatus().subscribe((status: string) => {
    _statusCb?.(status === 'connected')
    if (status === 'connected') {
      console.info('[gz-ws] connecté — souscription aux poses')
      _transport?.subscribe(new Topic(DYNAMIC_POSE_TOPIC, handlePoseV))
      _transport?.subscribe(new Topic(POSE_TOPIC, handlePoseV))
    } else if (status === 'error' || status === 'disconnected') {
      console.warn(`[gz-ws] ${status}`)
    }
  })

  _transport.connect(GZ_WS_URL)
}

export function disconnectGazebo(): void {
  if (_transport) { _transport.disconnect(); _transport = null }
  _positions.clear()
  _statusCb?.(false)
}
