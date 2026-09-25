import { Fragment, useEffect, useState } from 'react'
import { ApiError, listWorklistOrders, recordOrderResult } from '../api'
import type { OrderResultItemInput, OrderStatus, WorklistOrder } from '../types'
import { formatDateTime } from '../format'

// The Lab/Radiology Worklist (OPD/HIMS master spec audit's "4
// previously-skipped large items" list): the first screen LAB_TECH
// actually differs on, rather than reusing the doctor's own narrower
// ConsultationWorkspace-minus-a-section. Cross-patient by design --
// GET /orders/worklist (app/api/orders.py) spans every open LAB/
// RADIOLOGY order in the hospital, unlike every other order endpoint
// (scoped to one appointment's own encounter). Reuses the same
// result-entry endpoint/form shape ConsultationWorkspace's Orders tab
// already uses -- recording a result here and from inside a specific
// patient's consultation are the same action, just reached from
// opposite directions (by patient vs. by pending work).

const TYPE_OPTIONS: { value: 'LAB' | 'RADIOLOGY' | ''; label: string }[] = [
  { value: '', label: 'All types' },
  { value: 'LAB', label: 'Laboratory' },
  { value: 'RADIOLOGY', label: 'Radiology' },
]

const STATUS_OPTIONS: { value: OrderStatus | ''; label: string }[] = [
  { value: '', label: 'Pending (open work)' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'CANCELLED', label: 'Cancelled' },
]

function blankResultItem(): OrderResultItemInput {
  return { parameter: '', result_value: '', unit: '', reference_range: '', is_abnormal: false, is_critical: false }
}

export default function LabRadiologyWorklistPanel({ canRecordResults }: { canRecordResults: boolean }) {
  const [orderType, setOrderType] = useState<'LAB' | 'RADIOLOGY' | ''>('')
  const [status, setStatus] = useState<OrderStatus | ''>('')
  const [orders, setOrders] = useState<WorklistOrder[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [resultTargetId, setResultTargetId] = useState<number | null>(null)
  const [resultItems, setResultItems] = useState<OrderResultItemInput[]>([])
  const [resultSaving, setResultSaving] = useState(false)

  function load() {
    setLoading(true)
    setError(null)
    listWorklistOrders({ orderType: orderType || undefined, status: status || undefined })
      .then(setOrders)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the worklist'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [orderType, status])

  function startRecordResult(orderId: number) {
    setResultTargetId(orderId)
    setResultItems([blankResultItem()])
  }

  function cancelRecordResult() {
    setResultTargetId(null)
    setResultItems([])
  }

  function updateResultItem(index: number, patch: Partial<OrderResultItemInput>) {
    setResultItems((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }

  async function handleSaveResult(order: WorklistOrder) {
    const items = resultItems
      .filter((item) => item.parameter.trim() && item.result_value.trim())
      .map((item) => ({
        ...item,
        unit: item.unit?.trim() || undefined,
        reference_range: item.reference_range?.trim() || undefined,
      }))
    if (items.length === 0) return

    setResultSaving(true)
    setError(null)
    try {
      await recordOrderResult(order.appointment_id, order.id, items)
      setResultTargetId(null)
      setResultItems([])
      // The order just left "pending" (now COMPLETED) -- drop it from
      // the list locally instead of a full reload, same as any other
      // optimistic-remove-on-action list in this app.
      setOrders((prev) => prev.filter((o) => o.id !== order.id))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record the result')
    } finally {
      setResultSaving(false)
    }
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Lab Worklist</h2>
          <p className="muted">Every open lab/radiology order across all patients, oldest and most urgent first.</p>
        </div>
      </div>

      <form className="inline-form wrap" onSubmit={(e) => e.preventDefault()}>
        <select aria-label="Filter by order type" value={orderType} onChange={(e) => setOrderType(e.target.value as typeof orderType)}>
          {TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <button type="button" className="btn-secondary btn btn-sm" onClick={load} disabled={loading}>
          Refresh
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}

      {!loading && orders.length === 0 && <div className="state-block empty">Nothing here right now.</div>}

      {!loading && orders.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Patient</th>
              <th>Type</th>
              <th>Description</th>
              <th>Priority</th>
              <th>Doctor</th>
              <th>Ordered</th>
              {canRecordResults && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => (
              <Fragment key={order.id}>
                <tr>
                  <td>
                    {order.patient_name}
                    <div className="muted">{order.uhid}</div>
                  </td>
                  <td>{order.order_type === 'LAB' ? 'Laboratory' : order.order_type === 'RADIOLOGY' ? 'Radiology' : order.order_type}</td>
                  <td>
                    {order.description}
                    {order.clinical_indication && <div className="muted">{order.clinical_indication}</div>}
                  </td>
                  <td>{order.priority}</td>
                  <td>{order.doctor_name}</td>
                  <td>{formatDateTime(order.ordered_at)}</td>
                  {canRecordResults && (
                    <td>
                      {resultTargetId !== order.id && (order.status === 'ORDERED' || order.status === 'IN_PROGRESS') && (
                        <button type="button" className="btn btn-sm" onClick={() => startRecordResult(order.id)}>
                          Record result
                        </button>
                      )}
                    </td>
                  )}
                </tr>

                {resultTargetId === order.id && (
                  <tr>
                    <td colSpan={canRecordResults ? 7 : 6}>
                      {resultItems.map((item, index) => (
                        <div key={index} className="doctor-form-grid">
                          <label className="inline-label">
                            Parameter
                            <input
                              type="text"
                              value={item.parameter}
                              placeholder="e.g. Hemoglobin, Findings"
                              onChange={(e) => updateResultItem(index, { parameter: e.target.value })}
                            />
                          </label>
                          <label className="inline-label">
                            Result
                            <input
                              type="text"
                              value={item.result_value}
                              onChange={(e) => updateResultItem(index, { result_value: e.target.value })}
                            />
                          </label>
                          <label className="inline-label">
                            Unit
                            <input type="text" value={item.unit ?? ''} onChange={(e) => updateResultItem(index, { unit: e.target.value })} />
                          </label>
                          <label className="inline-label">
                            Reference range
                            <input
                              type="text"
                              value={item.reference_range ?? ''}
                              onChange={(e) => updateResultItem(index, { reference_range: e.target.value })}
                            />
                          </label>
                          <label className="inline-label checkbox-label">
                            <input
                              type="checkbox"
                              checked={item.is_abnormal ?? false}
                              onChange={(e) => updateResultItem(index, { is_abnormal: e.target.checked })}
                            />
                            Abnormal
                          </label>
                          <label className="inline-label checkbox-label">
                            <input
                              type="checkbox"
                              checked={item.is_critical ?? false}
                              onChange={(e) => updateResultItem(index, { is_critical: e.target.checked })}
                            />
                            Critical
                          </label>
                        </div>
                      ))}
                      <div className="doctor-quick-actions">
                        <button
                          type="button"
                          className="btn-secondary btn btn-sm"
                          onClick={() => setResultItems((prev) => [...prev, blankResultItem()])}
                        >
                          + Add parameter
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={resultSaving || !resultItems.some((i) => i.parameter.trim() && i.result_value.trim())}
                          onClick={() => handleSaveResult(order)}
                        >
                          {resultSaving ? 'Saving…' : 'Save result'}
                        </button>
                        <button type="button" className="btn-secondary btn btn-sm" onClick={cancelRecordResult}>
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
