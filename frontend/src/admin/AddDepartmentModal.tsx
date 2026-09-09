import { useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import { ApiError, createDepartment } from '../api'
import type { Department } from '../types'

// Same createPortal + modal-overlay/modal-panel shape as
// AppointmentDetailsModal.tsx, reused rather than a new modal
// primitive -- this app has no generic Dialog component yet, and one
// plain form field doesn't need Radix's Dialog for focus-trapping/etc.
export default function AddDepartmentModal({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (department: Department) => void
}) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const created = await createDepartment(name.trim())
      onCreated(created)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create department')
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
        aria-label="Add department"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">Add Department</h3>

        {error && <p className="error">{error}</p>}

        <form onSubmit={handleSubmit}>
          <label className="inline-label" style={{ width: '100%', marginBottom: 'var(--space-4)' }}>
            Department name
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter department name…"
              required
            />
          </label>

          <div className="payment-form-actions">
            <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-sm" disabled={busy || !name.trim()}>
              {busy ? 'Creating…' : 'Create Department'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
