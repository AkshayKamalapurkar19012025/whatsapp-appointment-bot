import { useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import { ApiError, createPatientAdmin } from '../api'
import type { Patient } from '../types'
import PhoneInput from '../PhoneInput'

// Same createPortal + modal-overlay/modal-panel shape as
// AddDepartmentModal.tsx/AddDoctorModal.tsx. The fields and the
// createPatientAdmin call are exactly what BookAppointmentPanel's old
// inline "+ New patient…" rows already used -- pulling them into a
// modal is a presentation change only, not a second registration
// system: there's still exactly one way a staff member creates a
// patient record (PatientsPanel's own inline form uses the identical
// two fields against the same endpoint).
export default function RegisterPatientModal({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (patient: Patient) => void
}) {
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const created = await createPatientAdmin(name.trim(), phone)
      onCreated(created)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not register the new patient')
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Register new patient"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">Register new patient</h3>
        {error && <p className="error">{error}</p>}

        <form onSubmit={handleSubmit}>
          <label className="inline-label" style={{ width: '100%' }}>
            Full name
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Patient's name" required />
          </label>
          <label className="inline-label" style={{ width: '100%', marginTop: 'var(--space-3)' }}>
            Mobile number
            <PhoneInput value={phone} onChange={setPhone} />
          </label>

          <div className="payment-form-actions" style={{ marginTop: 'var(--space-4)' }}>
            <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-sm" disabled={busy || !name.trim() || !phone}>
              {busy ? 'Registering…' : 'Register patient'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
