import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import AdminApp from './admin/AdminApp.tsx'
import DisplayBoard from './DisplayBoard.tsx'

// WEB P11: the staff/admin UI lives at /admin (aliased at /staff, since
// that's the term the rest of this app's domain language uses -- staff
// accounts, staff login, staff tokens), the patient UI everywhere else --
// one Vite app, no router dependency needed for a two/three-path split
// this small (see the WEB P11 report for the production static-hosting
// note this implies: the host must fall back to index.html for /admin,
// /staff, and /display alike, the same SPA-fallback requirement any
// client-routed path already has).
//
// /display (migrations/0027) is the third, deliberately unauthenticated
// path -- a waiting-room TV/kiosk browser, never a staff session, so it
// must never render AdminApp (which would show a staff login screen to
// anyone walking up to the lobby display) or App (the patient booking
// flow, equally out of place there).
const path = window.location.pathname
const isAdminPath = path.startsWith('/admin') || path.startsWith('/staff')
const isDisplayPath = path.startsWith('/display')

createRoot(document.getElementById('root')!).render(
  <StrictMode>{isDisplayPath ? <DisplayBoard /> : isAdminPath ? <AdminApp /> : <App />}</StrictMode>,
)
