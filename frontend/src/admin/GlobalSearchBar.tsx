import { useEffect, useRef, useState } from 'react'
import { MagnifyingGlass } from '@phosphor-icons/react'
import { ApiError, globalSearch } from '../api'
import type { Patient, SearchResults } from '../types'
import { formatDateTime } from '../format'
import PatientTimelineModal from './PatientTimelineModal'

// Master spec section 14: "no cross-entity search bar (UHID/name/
// mobile/appointment/encounter/order/bill from one box) exists
// anywhere -- only per-page search fields scoped to that page's own
// list." Patients and appointments only (see
// app/services/search_service.py's own docstring for why encounters/
// orders/bills don't need their own branch here -- Patient 360 already
// shows all of those once you've found the patient).
//
// Same debounced-search pattern as PatientsPanel/BookAppointmentPanel
// (300ms, cancelled-guard). Clicking a patient result opens
// PatientTimelineModal directly (self-contained, no navigation needed);
// clicking an appointment result hands off to the caller, since only
// AdminApp knows how to route to the right section for it.
export default function GlobalSearchBar({
  onOpenAppointment,
}: {
  onOpenAppointment: (appointmentId: number, status: string) => void
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [results, setResults] = useState<SearchResults | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [timelineTarget, setTimelineTarget] = useState<Patient | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed) {
      setResults(null)
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    const timer = setTimeout(() => {
      globalSearch(trimmed)
        .then((result) => {
          if (cancelled) return
          setResults(result)
        })
        .catch((err) => {
          if (cancelled) return
          setError(err instanceof ApiError ? err.message : 'Search failed')
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const hasResults = results && (results.patients.length > 0 || results.appointments.length > 0)
  const showDropdown = open && query.trim().length > 0

  return (
    <div className="global-search" ref={containerRef}>
      <label className="filter-bar-search-input global-search-input">
        <MagnifyingGlass size={16} aria-hidden="true" />
        <input
          type="search"
          placeholder="Search patients, appointments…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          aria-label="Global search"
        />
      </label>

      {showDropdown && (
        <div className="global-search-dropdown">
          {loading && <div className="global-search-status">Searching…</div>}
          {error && <div className="global-search-status error">{error}</div>}
          {!loading && !error && results && !hasResults && (
            <div className="global-search-status">No matches for &quot;{query.trim()}&quot;.</div>
          )}

          {results && results.patients.length > 0 && (
            <div className="global-search-group">
              <span className="global-search-group-label">Patients</span>
              {results.patients.map((p) => (
                <button
                  type="button"
                  key={`patient-${p.id}`}
                  className="global-search-result"
                  onClick={() => {
                    setTimelineTarget(p)
                    setOpen(false)
                    setQuery('')
                  }}
                >
                  <strong>{p.name}</strong>
                  <span className="muted">
                    {p.uhid} · {p.whatsapp_number}
                  </span>
                </button>
              ))}
            </div>
          )}

          {results && results.appointments.length > 0 && (
            <div className="global-search-group">
              <span className="global-search-group-label">Appointments</span>
              {results.appointments.map((a) => (
                <button
                  type="button"
                  key={`appointment-${a.id}`}
                  className="global-search-result"
                  onClick={() => {
                    onOpenAppointment(a.id, a.status)
                    setOpen(false)
                    setQuery('')
                  }}
                >
                  <strong>{a.patient_name}</strong>
                  <span className="muted">
                    {a.doctor_name} · {formatDateTime(a.start_at)} · {a.status}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {timelineTarget && <PatientTimelineModal patient={timelineTarget} onClose={() => setTimelineTarget(null)} />}
    </div>
  )
}
