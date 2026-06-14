import * as THREE from 'three';
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

// ── Trajectory trails ───────────────────────────────────────────────────────
// gzweb has no native trail system, so we draw one Three.js polyline per drone,
// sampling each model's world position straight from the live scene graph
// (gzweb already updates those from the /dynamic_pose/info websocket feed).
const TRAIL_DRONES = [
  { name: 'interceptor_1', color: 0x2ec4ff },
  { name: 'interceptor_2', color: 0x2ec4ff },
  { name: 'interceptor_3', color: 0x2ec4ff },
  { name: 'shahed_1', color: 0xff5d5d },
  { name: 'shahed_2', color: 0xff5d5d },
  { name: 'shahed_3', color: 0xff5d5d },
  { name: 'shahed_4', color: 0xff5d5d },
];
const TRAIL_MAX_POINTS = 220;   // tail length
const TRAIL_MIN_STEP = 1.5;     // metres between samples
const TRAIL_RESET_JUMP = 150;   // a jump bigger than this = respawn/reset → clear
const TRAIL_SAMPLE_MS = 100;

function startTrails() {
  const gzScene = sceneMgr.scene;            // gzweb Scene wrapper (TS-private at compile time only)
  const threeScene = gzScene && gzScene.scene;
  if (!threeScene) return;
  const trails = new Map();
  const tmp = new THREE.Vector3();

  const ensure = (d) => {
    let tr = trails.get(d.name);
    if (!tr) {
      const mat = new THREE.LineBasicMaterial({ color: d.color, transparent: true, opacity: 0.85 });
      const line = new THREE.Line(new THREE.BufferGeometry(), mat);
      line.frustumCulled = false;
      threeScene.add(line);
      tr = { pts: [], line };
      trails.set(d.name, tr);
    }
    return tr;
  };

  setInterval(() => {
    for (const d of TRAIL_DRONES) {
      const obj = gzScene.getByName ? gzScene.getByName(d.name) : threeScene.getObjectByName(d.name);
      if (!obj) continue;
      obj.getWorldPosition(tmp);
      if (tmp.x === 0 && tmp.y === 0 && tmp.z === 0) continue;   // not placed yet
      const tr = ensure(d);
      const last = tr.pts[tr.pts.length - 1];
      if (last) {
        const jump = last.distanceTo(tmp);
        if (jump > TRAIL_RESET_JUMP) tr.pts.length = 0;          // respawn → restart trail
        else if (jump < TRAIL_MIN_STEP) continue;                // hasn't moved enough
      }
      tr.pts.push(tmp.clone());
      if (tr.pts.length > TRAIL_MAX_POINTS) tr.pts.shift();
      tr.line.geometry.setFromPoints(tr.pts);
    }
  }, TRAIL_SAMPLE_MS);
}

let cameraInitialized = false;

sceneMgr.getConnectionStatusAsObservable().subscribe((ready) => {
  const status = sceneMgr.getConnectionStatus();
  statusEl.textContent = status;
  statusEl.style.color = ready ? '#4f4' : (status === 'error' ? '#f44' : '#ccc');

  if (ready && sceneMgr.scene && !cameraInitialized) {
    cameraInitialized = true;
    setOverviewCamera();
    startTrails();
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
