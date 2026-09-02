import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Proxies /api to the FastAPI backend during development so the browser
// sees one origin -- avoids needing CORS configuration in this phase
// (CORS policy is explicitly a WEB P10 security-review topic, not P3's).
// A production deployment decides its own same-origin-vs-separate-deploy
// story later; this proxy is a dev-time convenience only.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
