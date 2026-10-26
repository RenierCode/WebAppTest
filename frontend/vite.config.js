import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In production, server.py serves the built dist/ folder directly on port
// 3000, so the SPA and the API share an origin and these proxies aren't
// needed. In dev (`npm run dev`), Vite runs on its own port and proxies
// /api and /ws through to the FastAPI process so relative fetch() calls
// and the WebSocket both just work without an env var.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:3000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:3000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
  },
})
