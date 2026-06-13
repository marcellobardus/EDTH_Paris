import { SceneManager } from 'gzweb';

const params = new URLSearchParams(window.location.search);
const defaultWs = `ws://${window.location.hostname}:9002`;
const wsUrl = params.get('ws') ?? defaultWs;

const statusEl = document.getElementById('status');
statusEl.textContent = `Connecting to ${wsUrl}…`;

const sceneMgr = new SceneManager({
  elementId: 'gz-scene',
  websocketUrl: wsUrl,
});

sceneMgr.getConnectionStatusAsObservable().subscribe((ready) => {
  const status = sceneMgr.getConnectionStatus();
  statusEl.textContent = status;
  statusEl.style.color = ready ? '#4f4' : (status === 'error' ? '#f44' : '#ccc');
});
