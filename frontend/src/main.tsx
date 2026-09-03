import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import AdminApp from './admin/AdminApp.tsx'

// WEB P11: the staff/admin UI lives at /admin, the patient UI everywhere
// else -- one Vite app, no router dependency needed for a two-path split
// this small (see the WEB P11 report for the production static-hosting
// note this implies: the host must fall back to index.html for /admin,
// the same SPA-fallback requirement any client-routed path already has).
const isAdminPath = window.location.pathname.startsWith('/admin')

createRoot(document.getElementById('root')!).render(
  <StrictMode>{isAdminPath ? <AdminApp /> : <App />}</StrictMode>,
)
