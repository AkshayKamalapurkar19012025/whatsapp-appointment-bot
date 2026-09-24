import { useEffect, useState } from 'react'
import { CaretLeft, CaretRight, MagnifyingGlass, UsersThree } from '@phosphor-icons/react'
import { ApiError, listPatientsAdmin } from '../api'
import type { Patient } from '../types'
import { formatDateTime } from '../format'
import { useStaggerReveal } from '../useStaggerReveal'
import PatientFormModal from './PatientFormModal'
import PatientTimelineModal from './PatientTimelineModal'
import PatientRegistrationSummaryModal from './PatientRegistrationSummaryModal'

const PAGE_SIZE = 50

// The patient MASTER REGISTRY -- "who is this person", not a booking
// workflow. Deliberately no permanent first-time/recurring status or
// filter here any more: a patient isn't permanently "first-time", so
// visit history is shown as a fact (Last Visit / Visits), not a
// stored/filterable attribute. Search is the only filter.
//
// Server-searched and server-paginated (GET /patients/admin, master
// spec section 62/80: never load the whole registry into the browser)
// -- search is debounced (300ms) the same way BookAppointmentPanel's
// patient search already is, and a stale response for a since-changed
// query/page is dropped via the `cancelled` guard, same pattern.
export default function PatientsPanel() {
  const [patients, setPatients] = useState<Patient[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [searchText, setSearchText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // 'add' opens PatientFormModal in create mode; a Patient opens it in
  // edit mode for that row (PATCH /patients/{id}). Both share the exact
  // same modal/component -- there is no second patient form anywhere.
  const [formTarget, setFormTarget] = useState<'add' | Patient | null>(null)
  // Opens PatientTimelineModal for the clicked row -- the "N visits"
  // count on its own was a dead end (no way to see when, with which
  // doctor, or what actually happened); this is the drill-in (OPD/HIMS
  // master spec Phase 10, section 44's Patient 360 view).
  const [historyTarget, setHistoryTarget] = useState<Patient | null>(null)
  // Opens PatientRegistrationSummaryModal for the clicked row -- the
  // printable Patient Registration Summary (Printing phase section 1).
  const [printTarget, setPrintTarget] = useState<Patient | null>(null)

  const tbodyRef = useStaggerReveal<HTMLTableSectionElement>([patients])

  // Reset to page 0 whenever the search term changes, so a narrowed
  // search never leaves the view stuck on a now-out-of-range page.
  useEffect(() => {
    setPage(0)
  }, [searchText])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const timer = setTimeout(
      () => {
        listPatientsAdmin({ search: searchText.trim() || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE })
          .then((result) => {
            if (cancelled) return
            setPatients(result.items)
            setTotal(result.total)
          })
          .catch((err) => {
            if (cancelled) return
            setError(err instanceof ApiError ? err.message : 'Could not load patients')
          })
          .finally(() => {
            if (!cancelled) setLoading(false)
          })
      },
      searchText ? 300 : 0,
    )
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [searchText, page])

  function handleSaved(saved: Patient) {
    setPatients((prev) => {
      const exists = prev.some((p) => p.id === saved.id)
      if (exists) return prev.map((p) => (p.id === saved.id ? { ...p, ...saved } : p))
      return prev
    })
    // A brand-new registration won't be in the current page's results
    // (it's alphabetically placed, not necessarily on this page) --
    // reloading the current page/search is simpler and more honest
    // than guessing where it landed.
    if (formTarget === 'add') {
      setSearchText('')
      setPage(0)
    }
  }

  const pageStart = total === 0 ? 0 : page * PAGE_SIZE + 1
  const pageEnd = Math.min(total, (page + 1) * PAGE_SIZE)
  const hasPrev = page > 0
  const hasNext = pageEnd < total

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Patients</h2>
          <p className="muted">Manage patient records and demographics.</p>
        </div>
        <button type="button" className="btn btn-sm" onClick={() => setFormTarget('add')}>
          + Register Patient
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      <label className="filter-bar-search-input patients-search-input">
        <MagnifyingGlass size={16} aria-hidden="true" />
        <input
          type="search"
          placeholder="Search by name, mobile number or UHID…"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          aria-label="Search by name, mobile number or UHID"
        />
      </label>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading patients…
        </div>
      )}
      {!loading && patients.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <UsersThree size={28} weight="light" />
          </span>
          {searchText ? 'No patients match your search.' : 'No patients yet.'}
        </div>
      )}

      {!loading && patients.length > 0 && (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>Patient</th>
                <th>UHID</th>
                <th>Mobile</th>
                <th>Last visit</th>
                <th>Visits</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody ref={tbodyRef}>
              {patients.map((p) => {
                const visits = p.appointment_count ?? 0
                return (
                  <tr key={p.id}>
                    <td>
                      <strong>{p.name}</strong>
                    </td>
                    <td className="muted">{p.uhid}</td>
                    <td>{p.whatsapp_number}</td>
                    <td>{p.last_visit_at ? formatDateTime(p.last_visit_at) : <span className="muted">—</span>}</td>
                    <td>
                      {visits === 0 ? (
                        <span className="muted">0 visits</span>
                      ) : (
                        <button type="button" className="link" onClick={() => setHistoryTarget(p)}>
                          {visits} visit{visits === 1 ? '' : 's'}
                        </button>
                      )}
                    </td>
                    <td>
                      <div className="doctor-quick-actions" style={{ marginBottom: 0 }}>
                        <button type="button" className="btn-secondary btn btn-sm" onClick={() => setFormTarget(p)}>
                          View / Edit
                        </button>
                        <button type="button" className="btn-secondary btn btn-sm" onClick={() => setPrintTarget(p)}>
                          Print Summary
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="admin-pagination">
            <span className="muted">
              {pageStart}–{pageEnd} of {total}
            </span>
            <div className="admin-pagination-actions">
              <button
                type="button"
                className="btn-secondary btn btn-sm"
                disabled={!hasPrev}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                aria-label="Previous page"
              >
                <CaretLeft size={14} weight="bold" />
              </button>
              <button
                type="button"
                className="btn-secondary btn btn-sm"
                disabled={!hasNext}
                onClick={() => setPage((p) => p + 1)}
                aria-label="Next page"
              >
                <CaretRight size={14} weight="bold" />
              </button>
            </div>
          </div>
        </>
      )}

      {formTarget && (
        <PatientFormModal
          mode={formTarget === 'add' ? 'create' : 'edit'}
          patient={formTarget === 'add' ? null : formTarget}
          title={formTarget === 'add' ? 'Register patient' : 'Edit patient'}
          onClose={() => setFormTarget(null)}
          onSaved={handleSaved}
        />
      )}

      {historyTarget && <PatientTimelineModal patient={historyTarget} onClose={() => setHistoryTarget(null)} />}

      {printTarget && (
        <PatientRegistrationSummaryModal patientId={printTarget.id} onClose={() => setPrintTarget(null)} />
      )}
    </section>
  )
}
