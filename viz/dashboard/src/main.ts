// Point d'entrée du frontend.
//
// Le dashboard "Interceptor Mind" (HTML + CSS + script vanilla) vit désormais
// directement dans index.html. Il expose son interface externe sur window :
//   window.__COP_EXTERNAL  → true désactive sa simulation interne
//   window.__COP_setData   → injecte un snapshot et redessine
//   window.__COP_onControl → callbacks des boutons Run / Reset
//
// On bascule en mode externe, puis on démarre le bridge qui alimente le dashboard
// depuis l'API (mock ou backend). Tout vit dans le même window : appels synchrones,
// latence minimale.

import { startBridge } from './cop_bridge'
import { startSim } from './api'

// Mode données externes — lu à chaque frame par le dashboard.
window.__COP_EXTERNAL = true

function boot(): void {
  void startSim()   // démarre la source de données (mock par défaut)
  startBridge()     // alimente le dashboard + câble les boutons Run / Reset
  console.info('[main] bridge started — external data feeding the dashboard')
}

// Le module est différé : le DOM (et le script du dashboard) est déjà parsé.
// On attend tout de même la fin du boot interne du dashboard si nécessaire.
if (typeof window.__COP_setData === 'function') {
  boot()
} else {
  // Le script vanilla du dashboard s'initialise sur DOMContentLoaded ; on attend
  // qu'il ait exposé son interface, puis on démarre.
  const t = setInterval(() => {
    if (typeof window.__COP_setData === 'function') {
      clearInterval(t)
      boot()
    }
  }, 30)
}
