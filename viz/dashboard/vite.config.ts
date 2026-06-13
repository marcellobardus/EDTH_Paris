import { defineConfig } from 'vite'

// Le dashboard standalone est servi tel quel comme index.html ; main.ts l'alimente.
// Proxy : les appels /api du frontend sont relayés vers le backend FastAPI (port 8000).
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
