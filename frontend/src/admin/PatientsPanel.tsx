import { useEffect, useState } from 'react'
import { UsersThree } from '@phosphor-icons/react'
import { ApiError, listPatients } from '../api'
import type { Patient } from '../types'
import { formatDate } from '../format'
import { useStaggerReveal } from '../useStaggerReveal'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import PatientFormModal from './PatientFormModal'

type PatientTypeFilter = 'all' | 'first-time' | 'recurring'

export default function PatientsPanel() {
  const [patients, setPatients] = useState<Patient[]>([])
  const [searchText, setSearchText] = useState('')
  const [typeFilter, setTypeFilter] = useState<PatientTypeFilter>('all')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // 'add' opens PatientFormModal in create mode; a Patient opens it in
  // edit mode for that row (PATCH /patients/{id}, migrations/0023 --
  // wired to the UI here for the first time). Both share the exact
  // same modal/component -- there is no second patient form anywhere.
  const [formTarget, setFormTarget] = useState<'add' | Patient | null>(null)

  // Both the free-text search and the first-time/recurring filter are
  // applied client-side over the already-loaded list, same pattern as
  // AppointmentsPanel's patient search -- the whole list is small enough
  // that a round trip per keystroke/toggle would be pure overhead.
  const searchNeedle = searchText.trim().toLowerCase()
  const visiblePatients = patients.filter((p) => {
    if (typeFilter !== 'all' && (p.patient_type ?? 'first-time') !== typeFilter) return false
    if (!searchNeedle) return true
    return (
      p.name.toLowerCase().includes(searchNeedle) ||
      p.whatsapp_number.toLowerCase().includes(searchNeedle) ||
      String(p.id).includes(searchNeedle)
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
      <h2>Patients</h2>
      <p className="muted">
        The permanent patient directory -- the same record WhatsApp, the patient web login, and every
        appointment (whatever its booking source) all read and write.
      </p>
      {error && <p className="error">{error}</p>}

      <button type="button" className="btn btn-sm" onClick={() => setFormTarget('add')}>
        + Add patient
      </button>

      <div className="filter-bar">
        <label className="inline-label">
          Search
          <input
            type="search"
            placeholder="Name, phone number, or patient ID"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </label>
        <label className="inline-label">
          Type
          <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v as PatientTypeFilter)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="first-time">First-time</SelectItem>
              <SelectItem value="recurring">Recurring</SelectItem>
            </SelectContent>
          </Select>
        </label>
        {(searchText || typeFilter !== 'all') && (
          <button
            type="button"
            className="btn-secondary btn btn-sm"
            onClick={() => {
              setSearchText('')
              setTypeFilter('all')
            }}
          >
            Clear filters
          </button>
        )}
      </div>

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
              <th>Contact number</th>
              <th>Date of birth</th>
              <th>Gender</th>
              <th>Type</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody ref={tbodyRef}>
            {visiblePatients.map((p) => {
              const patientType = p.patient_type ?? 'first-time'
              return (
                <tr key={p.id}>
                  <td>
                    <strong>{p.name}</strong>
                    <div className="muted">Patient ID: {p.id}</div>
                  </td>
                  <td>{p.whatsapp_number}</td>
                  <td>{p.date_of_birth ? formatDate(p.date_of_birth) : <span className="muted">—</span>}</td>
                  <td>{p.gender ?? <span className="muted">—</span>}</td>
                  <td>
                    <span className={`pill patient-${patientType}`}>
                      {patientType === 'recurring' ? 'Recurring' : 'First-time'}
                    </span>
                  </td>
                  <td>
                    <button type="button" className="btn-secondary btn btn-sm" onClick={() => setFormTarget(p)}>
                      Edit
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
          title={formTarget === 'add' ? 'Add patient' : 'Edit patient'}
          onClose={() => setFormTarget(null)}
          onSaved={handleSaved}
        />
      )}
    </section>
  )
}
