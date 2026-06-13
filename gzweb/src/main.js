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

function setOverviewCamera() {
  if (!sceneMgr.scene) return;
  sceneMgr.scene.camera.far = 5000;
  sceneMgr.scene.camera.updateProjectionMatrix();
  sceneMgr.scene.camera.position.set(300, -200, 400);
  sceneMgr.scene.controls.target.set(480, 480, 0);
  sceneMgr.scene.camera.lookAt(480, 480, 0);
  sceneMgr.scene.controls.update();
}

let cameraInitialized = false;

sceneMgr.getConnectionStatusAsObservable().subscribe((ready) => {
  const status = sceneMgr.getConnectionStatus();
  statusEl.textContent = status;
  statusEl.style.color = ready ? '#4f4' : (status === 'error' ? '#f44' : '#ccc');

  if (ready && sceneMgr.scene && !cameraInitialized) {
    cameraInitialized = true;
    setOverviewCamera();
    // After scene models have arrived from SceneBroadcaster, jump to interceptor_2
    setTimeout(() => sceneMgr.moveTo('interceptor_2'), 2500);
  }
});

document.getElementById('nav-panel').addEventListener('click', (e) => {
  const model = e.target.dataset.model;
  if (!model) return;
  if (model === 'overview') {
    setOverviewCamera();
  } else {
    sceneMgr.moveTo(model);
  }
});
