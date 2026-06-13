// Déclarations minimales pour la lib `gzweb` (pas de @types publiés).
// On ne type que ce qu'on utilise : Transport + Topic (couche transport,
// sans le rendu 3D). Le protocole gz-transport WebSocket et le décodage
// protobuf sont gérés en interne par la lib.
declare module 'gzweb' {
  import type { Observable } from 'rxjs'

  export type TopicCb = (msg: any) => void

  export class Topic {
    name: string
    cb: TopicCb
    constructor(name: string, cb: TopicCb)
  }

  export class Transport {
    connect(url: string, key?: string): void
    disconnect(): void
    subscribe(topic: Topic): void
    unsubscribe(name: string): void
    getConnectionStatus(): Observable<string>
    getWorld(): string
    getAvailableTopics(): object[]
  }
}
