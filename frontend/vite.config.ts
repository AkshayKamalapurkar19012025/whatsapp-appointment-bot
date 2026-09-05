import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Proxies /api to the FastAPI backend during development so the browser
// sees one origin -- avoids needing CORS configuration in this phase
// (CORS policy is explicitly a WEB P10 security-review topic, not P3's).
// A production deployment decides its own same-origin-vs-separate-deploy
// story later; this proxy is a dev-time convenience only.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Lets a Cloudflare Quick Tunnel (random *.trycloudflare.com hostname,
    // see scripts/start_tunnel.sh) reach this dev server -- Vite otherwise
    // rejects requests whose Host header isn't localhost, to block DNS
    // rebinding attacks. Only this one known Cloudflare suffix is trusted;
    // this does not widen CORS or any application-level auth.
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      // Dev/test-only diagnostic route (see its docstring in
      // app/api/patient_auth.py) that returns a patient's current OTP in
      // the clear -- nothing in this frontend ever calls it. Kept working
      // on localhost:8000 for a developer to curl directly, but not
      // reachable through this proxy (and therefore not through a tunnel
      // pointed at this dev server), since exposing it externally would
      // let anyone log in as any patient without owning their phone.
      // Must be listed before the generic '/api' entry below so its more
      // specific match wins.
      '/api/auth/patient/otp/_dev_lookup': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        bypass: () => false,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // Uploaded doctor profile photos (app/main.py's StaticFiles mount,
      // app/config.py's MEDIA_ROOT) -- same dev-time same-origin
      // convenience as /api above, so an <img src="/media/..."> URL
      // returned by the API actually resolves against this dev server.
      '/media': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
