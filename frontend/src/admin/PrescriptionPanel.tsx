import { useEffect, useState } from 'react'
import { Warning } from '@phosphor-icons/react'
import {
  ApiError,
  addPrescriptionItem,
  cancelPrescription,
  getPrescription,
  prescribePrescription,
  removePrescriptionItem,
} from '../api'
import type { Prescription, PrescriptionItemInput } from '../types'
import { formatDateTime } from '../format'

const BLANK_ITEM: PrescriptionItemInput = {
  medicine_name: '',
  generic_name: '',
  dosage: '',
  route: '',
  frequency: '',
  duration: '',
  quantity: 1,
  food_instructions: '',
  special_instructions: '',
}

// OPD/HIMS master spec Phase 8 -- the Prescription tab of Consultation
// Workspace.tsx. Self-contained and lazily loaded (fetches its own
// data when this tab is first opened) rather than threaded through the
// parent's combined initial load, unlike the Triage/Consultation/
// Orders tabs -- this component was added once that file was already
// over a thousand lines, and keeping the addition self-contained beat
// growing that file's already-tangled shared loading sequence further.
export default function PrescriptionPanel({
  appointmentId,
  appointmentCheckedIn,
}: {
  appointmentId: number
  appointmentCheckedIn: boolean
}) {
  const [prescription, setPrescription] = useState<Prescription | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notCheckedIn, setNotCheckedIn] = useState(false)

  const [form, setForm] = useState<PrescriptionItemInput>(BLANK_ITEM)
  const [adding, setAdding] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [removingId, setRemovingId] = useState<number | null>(null)
  const [prescribing, setPrescribing] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [showCancelForm, setShowCancelForm] = useState(false)
  const [cancelReason, setCancelReason] = useState('')

  useEffect(() => {
    let cancelledEffect = false
    setLoading(true)
    setLoadError(null)
    setNotCheckedIn(false)

    getPrescription(appointmentId)
      .then((p) => {
        if (!cancelledEffect) setPrescription(p)
      })
      .catch((err) => {
        if (cancelledEffect) return
        if (err instanceof ApiError && err.status === 409) {
          setNotCheckedIn(true)
        } else {
          setLoadError(err instanceof ApiError ? err.message : 'Could not load the prescription')
        }
      })
      .finally(() => {
        if (!cancelledEffect) setLoading(false)
      })

    return () => {
      cancelledEffect = true
    }
  }, [appointmentId])

  async function handleAddItem() {
    setAdding(true)
    setActionError(null)
    try {
      const updated = await addPrescriptionItem(appointmentId, {
        ...form,
        generic_name: form.generic_name?.trim() || undefined,
        dosage: form.dosage?.trim() || undefined,
        route: form.route?.trim() || undefined,
        frequency: form.frequency?.trim() || undefined,
        duration: form.duration?.trim() || undefined,
        food_instructions: form.food_instructions?.trim() || undefined,
        special_instructions: form.special_instructions?.trim() || undefined,
      })
      setPrescription(updated)
      setForm(BLANK_ITEM)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not add this medicine')
    } finally {
      setAdding(false)
    }
  }

  async function handleRemoveItem(itemId: number) {
    setRemovingId(itemId)
    setActionError(null)
    try {
      setPrescription(await removePrescriptionItem(appointmentId, itemId))
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not remove this medicine')
    } finally {
      setRemovingId(null)
    }
  }

  async function handlePrescribe() {
    setPrescribing(true)
    setActionError(null)
    try {
      setPrescription(await prescribePrescription(appointmentId))
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not send this prescription to pharmacy')
    } finally {
      setPrescribing(false)
    }
  }

  async function handleCancel() {
    if (!cancelReason.trim()) return
    setCancelling(true)
    setActionError(null)
    try {
      setPrescription(await cancelPrescription(appointmentId, cancelReason.trim()))
      setShowCancelForm(false)
      setCancelReason('')
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not cancel this prescription')
    } finally {
      setCancelling(false)
    }
  }

  if (loading) {
    return (
      <div className="state-block">
        <span className="spinner" aria-hidden="true" />
        Loading…
      </div>
    )
  }

  if (loadError) {
    return <p className="error">{loadError}</p>
  }

  if (notCheckedIn || !prescription) {
    return (
      <div className="state-block">
        <Warning size={20} weight="regular" />
        This patient isn&apos;t currently checked in. A prescription can only be started while the visit is in
        progress.
      </div>
    )
  }

  const editable = prescription.status === 'DRAFT' && appointmentCheckedIn
  const canPrescribe = editable && prescription.items.length > 0
  const canCancel = prescription.status === 'PRESCRIBED' && !prescription.items.some((i) => i.quantity_dispensed > 0)

  return (
    <div className="detail-section">
      {prescription.status !== 'DRAFT' && (
        <p className="muted">
          {prescription.status === 'PRESCRIBED' &&
            `Sent to pharmacy ${prescription.prescribed_at ? formatDateTime(prescription.prescribed_at) : ''}`}
          {prescription.status === 'CANCELLED' && `Cancelled: ${prescription.cancel_reason ?? ''}`}
        </p>
      )}
      {actionError && <p className="error">{actionError}</p>}

      {editable && (
        <div className="doctor-form-grid">
          <label className="inline-label doctor-form-full">
            Medicine *
            <input
              type="text"
              value={form.medicine_name}
              onChange={(e) => setForm({ ...form, medicine_name: e.target.value })}
              placeholder="e.g. Paracetamol"
            />
          </label>
          <label className="inline-label">
            Generic name
            <input
              type="text"
              value={form.generic_name}
              onChange={(e) => setForm({ ...form, generic_name: e.target.value })}
            />
          </label>
          <label className="inline-label">
            Dosage
            <input
              type="text"
              value={form.dosage}
              onChange={(e) => setForm({ ...form, dosage: e.target.value })}
              placeholder="e.g. 500mg"
            />
          </label>
          <label className="inline-label">
            Route
            <input
              type="text"
              value={form.route}
              onChange={(e) => setForm({ ...form, route: e.target.value })}
              placeholder="e.g. Oral"
            />
          </label>
          <label className="inline-label">
            Frequency
            <input
              type="text"
              value={form.frequency}
              onChange={(e) => setForm({ ...form, frequency: e.target.value })}
              placeholder="e.g. 1-0-1"
            />
          </label>
          <label className="inline-label">
            Duration
            <input
              type="text"
              value={form.duration}
              onChange={(e) => setForm({ ...form, duration: e.target.value })}
              placeholder="e.g. 5 days"
            />
          </label>
          <label className="inline-label">
            Quantity *
            <input
              type="number"
              min={1}
              value={form.quantity}
              onChange={(e) => setForm({ ...form, quantity: Number(e.target.value) })}
            />
          </label>
          <label className="inline-label doctor-form-full">
            Food instructions
            <input
              type="text"
              value={form.food_instructions}
              onChange={(e) => setForm({ ...form, food_instructions: e.target.value })}
              placeholder="e.g. After food"
            />
          </label>
          <label className="inline-label doctor-form-full">
            Special instructions
            <input
              type="text"
              value={form.special_instructions}
              onChange={(e) => setForm({ ...form, special_instructions: e.target.value })}
            />
          </label>
          <div className="doctor-form-full">
            <button
              type="button"
              className="btn-secondary btn"
              disabled={adding || !form.medicine_name.trim() || !form.quantity}
              onClick={handleAddItem}
            >
              {adding ? 'Adding…' : '+ Add medicine'}
            </button>
          </div>
        </div>
      )}

      {prescription.items.length === 0 && <p className="muted">No medicines added yet.</p>}

      {prescription.items.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Medicine</th>
              <th>Dosage</th>
              <th>Frequency</th>
              <th>Duration</th>
              <th>Qty</th>
              <th>Status</th>
              {editable && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {prescription.items.map((item) => (
              <tr key={item.id}>
                <td>
                  {item.medicine_name}
                  {item.generic_name && <div className="muted">{item.generic_name}</div>}
                  {(item.food_instructions || item.special_instructions) && (
                    <div className="muted">
                      {[item.food_instructions, item.special_instructions].filter(Boolean).join(' · ')}
                    </div>
                  )}
                </td>
                <td>{item.dosage || '—'}</td>
                <td>{item.frequency || '—'}</td>
                <td>{item.duration || '—'}</td>
                <td>
                  {item.quantity_dispensed}/{item.quantity}
                </td>
                <td>
                  <span className={`pill status-${item.dispense_status.toLowerCase()}`}>
                    {item.dispense_status.replace('_', ' ')}
                  </span>
                </td>
                {editable && (
                  <td>
                    <button
                      type="button"
                      className="btn-danger btn btn-sm"
                      disabled={removingId === item.id}
                      onClick={() => handleRemoveItem(item.id)}
                    >
                      {removingId === item.id ? 'Removing…' : 'Remove'}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {(canPrescribe || canCancel) && (
        <div className="doctor-quick-actions">
          {canPrescribe && (
            <button type="button" className="btn" disabled={prescribing} onClick={handlePrescribe}>
              {prescribing ? 'Sending…' : 'Send to pharmacy'}
            </button>
          )}
          {canCancel &&
            (showCancelForm ? (
              <div className="queue-priority-form">
                <input
                  type="text"
                  placeholder="Reason (required)"
                  value={cancelReason}
                  onChange={(e) => setCancelReason(e.target.value)}
                  autoFocus
                />
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={!cancelReason.trim() || cancelling}
                  onClick={handleCancel}
                >
                  {cancelling ? 'Cancelling…' : 'Confirm'}
                </button>
                <button type="button" className="btn-secondary btn btn-sm" onClick={() => setShowCancelForm(false)}>
                  Back
                </button>
              </div>
            ) : (
              <button type="button" className="btn-danger btn btn-sm" onClick={() => setShowCancelForm(true)}>
                Cancel prescription
              </button>
            ))}
        </div>
      )}
    </div>
  )
}
