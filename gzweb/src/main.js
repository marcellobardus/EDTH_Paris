import { SceneManager } from 'gzweb';

const params = new URLSearchParams(window.location.search);
const wsUrl = params.get('ws') ?? 'ws://localhost:9002';

const statusEl = document.getElementById('status');

const sceneMgr = new SceneManager({
  elementId: 'gz-scene',
  websocketUrl: wsUrl,
});

sceneMgr.getConnectionStatusAsObservable().subscribe((ready) => {
  statusEl.textContent = ready ? `Connected — ${wsUrl}` : `Connecting to ${wsUrl}…`;
});
