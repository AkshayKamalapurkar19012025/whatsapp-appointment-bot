import { useEffect, useState } from 'react'
import { UsersThree } from '@phosphor-icons/react'
import { ApiError, createPatientAdmin, listPatients } from '../api'
import type { Patient } from '../types'
import PhoneInput from '../PhoneInput'
import { useStaggerReveal } from '../useStaggerReveal'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'

type PatientTypeFilter = 'all' | 'first-time' | 'recurring'

export default function PatientsPanel() {
  const [patients, setPatients] = useState<Patient[]>([])
  const [name, setName] = useState('')
  const [whatsappNumber, setWhatsappNumber] = useState('')
  const [searchText, setSearchText] = useState('')
  const [typeFilter, setTypeFilter] = useState<PatientTypeFilter>('all')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  // Both the free-text search and the first-time/recurring filter are
  // applied client-side over the already-loaded list, same pattern as
  // AppointmentsPanel's patient search -- the whole list is small enough
  // that a round trip per keystroke/toggle would be pure overhead.
  const searchNeedle = searchText.trim().toLowerCase()
  const visiblePatients = patients.filter((p) => {
    if (typeFilter !== 'all' && (p.patient_type ?? 'first-time') !== typeFilter) return false
    if (!searchNeedle) return true
    return p.name.toLowerCase().includes(searchNeedle) || p.whatsapp_number.toLowerCase().includes(searchNeedle)
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

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await createPatientAdmin(name, whatsappNumber)
      setName('')
      setWhatsappNumber('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create patient')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Patients</h2>
      <p className="muted">
        A patient record can also be created here directly (e.g. registering someone over the
        phone) -- the same table WhatsApp and the patient web login write to.
      </p>
      {error && <p className="error">{error}</p>}

      <form className="inline-form wrap" onSubmit={handleCreate}>
        <label className="inline-label">
          Name
          <input placeholder="Full name" value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="inline-label">
          Mobile number
          <PhoneInput value={whatsappNumber} onChange={setWhatsappNumber} />
        </label>
        <button type="submit" style={{ width: 'auto' }} disabled={busy}>
          {busy ? 'Creating…' : 'Add patient'}
        </button>
      </form>

      <div className="filter-bar">
        <label className="inline-label">
          Search
          <input
            type="search"
            placeholder="Name or number"
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
            className="btn-secondary btn"
            style={{ width: 'auto' }}
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
              <th>Name</th>
              <th>Contact number</th>
              <th>Type</th>
            </tr>
          </thead>
          <tbody ref={tbodyRef}>
            {visiblePatients.map((p) => {
              const patientType = p.patient_type ?? 'first-time'
              return (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>{p.whatsapp_number}</td>
                  <td>
                    <span className={`pill patient-${patientType}`}>
                      {patientType === 'recurring' ? 'Recurring' : 'First-time'}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
