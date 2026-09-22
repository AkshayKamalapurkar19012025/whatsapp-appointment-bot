import { useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import { ApiError, createPackage, updatePackage } from '../api'
import type { Package } from '../types'

// Same createPortal + modal-overlay/modal-panel shape as
// PatientFormModal.tsx -- one form, both create and edit, matching
// this codebase's usual single-modal-per-entity convention.
export default function PackageFormModal({
  mode,
  pkg,
  onClose,
  onSaved,
}: {
  mode: 'create' | 'edit'
  pkg?: Package | null
  onClose: () => void
  onSaved: (pkg: Package) => void
}) {
  const [name, setName] = useState(pkg?.name ?? '')
  const [description, setDescription] = useState(pkg?.description ?? '')
  const [price, setPrice] = useState(pkg ? String(pkg.price) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const payload = { name: name.trim(), description: description.trim() || null, price: Number(price) }
      const saved = mode === 'edit' && pkg ? await updatePackage(pkg.id, payload) : await createPackage(payload)
      onSaved(saved)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save this package')
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
        aria-label={mode === 'edit' ? 'Edit package' : 'Add package'}
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <h3 className="appointment-details-heading">{mode === 'edit' ? 'Edit package' : 'Add package'}</h3>
        {error && <p className="error">{error}</p>}

        <form onSubmit={handleSubmit}>
          <label className="inline-label" style={{ width: '100%' }}>
            Name
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Health Checkup Basic"
              required
            />
          </label>
          <label className="inline-label" style={{ width: '100%', marginTop: 'var(--space-3)' }}>
            Description
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this package includes (for staff reference)"
              rows={3}
            />
          </label>
          <label className="inline-label" style={{ width: '100%', marginTop: 'var(--space-3)' }}>
            Price (₹)
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              required
            />
          </label>

          <button type="submit" className="btn" style={{ marginTop: 'var(--space-4)' }} disabled={busy}>
            {busy ? 'Saving…' : mode === 'edit' ? 'Save changes' : 'Add package'}
          </button>
        </form>
      </div>
    </div>,
    document.body,
  )
}
