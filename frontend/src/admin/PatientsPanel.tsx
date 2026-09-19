import { useEffect, useState } from 'react'
import { MagnifyingGlass, UsersThree } from '@phosphor-icons/react'
import { ApiError, listPatients } from '../api'
import type { Patient } from '../types'
import { formatDateTime } from '../format'
import { useStaggerReveal } from '../useStaggerReveal'
import PatientFormModal from './PatientFormModal'

// The patient MASTER REGISTRY -- "who is this person", not a booking
// workflow. Deliberately no permanent first-time/recurring status or
// filter here any more: a patient isn't permanently "first-time", so
// visit history is shown as a fact (Last Visit / Visits), not a
// stored/filterable attribute. Search is the only filter -- if real
// usage later shows a lighter visit-based filter is actually needed,
// add it then rather than keeping the old framing around "just in case".
export default function PatientsPanel() {
  const [patients, setPatients] = useState<Patient[]>([])
  const [searchText, setSearchText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // 'add' opens PatientFormModal in create mode; a Patient opens it in
  // edit mode for that row (PATCH /patients/{id}). Both share the exact
  // same modal/component -- there is no second patient form anywhere.
  const [formTarget, setFormTarget] = useState<'add' | Patient | null>(null)

  const searchNeedle = searchText.trim().toLowerCase()
  const visiblePatients = patients.filter((p) => {
    if (!searchNeedle) return true
    return (
      p.name.toLowerCase().includes(searchNeedle) ||
      p.whatsapp_number.toLowerCase().includes(searchNeedle) ||
      p.uhid.toLowerCase().includes(searchNeedle)
    )
  })
  const tbodyRef = useStaggerReveal<HTMLTableSectionElement>([patients])

  function load() {
    setLoading(true)
    listPatients()
      .then(setPatients)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load patients'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  function handleSaved(saved: Patient) {
    setPatients((prev) => {
      const exists = prev.some((p) => p.id === saved.id)
      if (exists) return prev.map((p) => (p.id === saved.id ? { ...p, ...saved } : p))
      return [...prev, saved]
    })
  }

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
      {!loading && visiblePatients.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <UsersThree size={28} weight="light" />
          </span>
          {patients.length === 0 ? 'No patients yet.' : 'No patients match your search.'}
        </div>
      )}

      {!loading && visiblePatients.length > 0 && (
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
            {visiblePatients.map((p) => {
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
                    {visits} visit{visits === 1 ? '' : 's'}
                  </td>
                  <td>
                    <button type="button" className="btn-secondary btn btn-sm" onClick={() => setFormTarget(p)}>
                      View / Edit
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
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
    </section>
  )
}
