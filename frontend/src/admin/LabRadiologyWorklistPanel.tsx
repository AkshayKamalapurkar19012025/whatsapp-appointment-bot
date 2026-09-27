import { Fragment, useEffect, useState } from 'react'
import {
  ApiError,
  collectSample,
  listWorklistOrders,
  recordOrderResult,
  rejectSample,
  releaseOrderResult,
  startOrderProcessing,
  verifyOrderResult,
} from '../api'
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
//
// Phase 7 (migrations/0054_diagnostic_workflow.sql): the worklist now
// drives the full sample-collection -> processing -> result-entry ->
// verification -> release lifecycle, not just result entry. One
// action per row at a time, chosen from the order's own status --
// same "the backend owns the state machine, the UI just shows the one
// next legal action" discipline every other status-driven action in
// this app already follows (e.g. QueueSection's hold/recall/priority
// buttons).

const TYPE_OPTIONS: { value: 'LAB' | 'RADIOLOGY' | ''; label: string }[] = [
  { value: '', label: 'All types' },
  { value: 'LAB', label: 'Laboratory' },
  { value: 'RADIOLOGY', label: 'Radiology' },
]

const STATUS_OPTIONS: { value: OrderStatus | ''; label: string }[] = [
  { value: '', label: 'Pending (open work)' },
  { value: 'COMPLETED', label: 'Completed (released)' },
  { value: 'CANCELLED', label: 'Cancelled' },
]

const STATUS_LABELS: Record<OrderStatus, string> = {
  ORDERED: 'Ordered',
  COLLECTED: 'Sample collected',
  IN_PROGRESS: 'In progress',
  RESULT_ENTERED: 'Awaiting verification',
  VERIFIED: 'Awaiting release',
  COMPLETED: 'Released',
  CANCELLED: 'Cancelled',
}

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

  const [collectTargetId, setCollectTargetId] = useState<number | null>(null)
  const [sampleType, setSampleType] = useState('')
  const [sampleNotes, setSampleNotes] = useState('')
  const [collectSaving, setCollectSaving] = useState(false)

  const [busyOrderId, setBusyOrderId] = useState<number | null>(null)

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
      // Now RESULT_ENTERED (LAB/RADIOLOGY) or COMPLETED (every other
      // order type) -- either way it's no longer showing the "Record
      // result" action, so a full reload keeps the row's status/next
      // action correct without special-casing which case this was.
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record the result')
    } finally {
      setResultSaving(false)
    }
  }

  function startCollectSample(orderId: number) {
    setCollectTargetId(orderId)
    setSampleType('')
    setSampleNotes('')
  }

  function cancelCollectSample() {
    setCollectTargetId(null)
    setSampleType('')
    setSampleNotes('')
  }

  async function handleCollectSample(order: WorklistOrder) {
    if (!sampleType.trim()) return
    setCollectSaving(true)
    setError(null)
    try {
      await collectSample(order.appointment_id, order.id, sampleType.trim(), sampleNotes.trim() || undefined)
      setCollectTargetId(null)
      setSampleType('')
      setSampleNotes('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record the sample collection')
    } finally {
      setCollectSaving(false)
    }
  }

  async function handleStartProcessing(order: WorklistOrder) {
    setBusyOrderId(order.id)
    setError(null)
    try {
      await startOrderProcessing(order.appointment_id, order.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not mark this order in-progress')
    } finally {
      setBusyOrderId(null)
    }
  }

  async function handleVerify(order: WorklistOrder) {
    setBusyOrderId(order.id)
    setError(null)
    try {
      await verifyOrderResult(order.appointment_id, order.id)
      load()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not verify this result',
      )
    } finally {
      setBusyOrderId(null)
    }
  }

  async function handleRelease(order: WorklistOrder) {
    setBusyOrderId(order.id)
    setError(null)
    try {
      await releaseOrderResult(order.appointment_id, order.id)
      // Released (COMPLETED) orders leave the default "pending" filter
      // -- drop it locally rather than a full reload when that's the
      // active filter, same optimistic-remove-on-terminal-action
      // pattern this screen already used before Phase 7.
      if (!status) {
        setOrders((prev) => prev.filter((o) => o.id !== order.id))
      } else {
        load()
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not release this result')
    } finally {
      setBusyOrderId(null)
    }
  }

  async function handleRejectSample(order: WorklistOrder, sampleId: number) {
    const reason = window.prompt('Reason for rejecting this sample:')
    if (!reason || !reason.trim()) return
    setBusyOrderId(order.id)
    setError(null)
    try {
      await rejectSample(order.appointment_id, order.id, sampleId, reason.trim())
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reject this sample')
    } finally {
      setBusyOrderId(null)
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
              <th>Status</th>
              <th>Ordered</th>
              {canRecordResults && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => {
              const isBusy = busyOrderId === order.id
              const formOpen = resultTargetId === order.id || collectTargetId === order.id
              return (
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
                  <td>
                    <span className={`pill status-${order.status.toLowerCase()}`}>{STATUS_LABELS[order.status]}</span>
                    {order.latest_sample_code && order.latest_sample_status === 'COLLECTED' && (
                      <div className="muted">{order.latest_sample_code} ({order.latest_sample_type})</div>
                    )}
                  </td>
                  <td>{formatDateTime(order.ordered_at)}</td>
                  {canRecordResults && (
                    <td>
                      {!formOpen && (
                        <div className="doctor-quick-actions">
                          {order.order_type === 'LAB' && order.status === 'ORDERED' && (
                            <button type="button" className="btn btn-sm" onClick={() => startCollectSample(order.id)}>
                              Collect sample
                            </button>
                          )}
                          {order.order_type === 'LAB' &&
                            order.status === 'COLLECTED' &&
                            order.latest_sample_status === 'COLLECTED' &&
                            order.latest_sample_id && (
                              <button
                                type="button"
                                className="btn-secondary btn btn-sm"
                                disabled={isBusy}
                                onClick={() => handleRejectSample(order, order.latest_sample_id as number)}
                              >
                                Reject sample
                              </button>
                            )}
                          {((order.order_type === 'LAB' && order.status === 'COLLECTED') ||
                            (order.order_type === 'RADIOLOGY' && order.status === 'ORDERED')) && (
                            <button
                              type="button"
                              className="btn btn-sm"
                              disabled={isBusy}
                              onClick={() => handleStartProcessing(order)}
                            >
                              {order.order_type === 'LAB' ? 'Start processing' : 'Mark performed'}
                            </button>
                          )}
                          {(order.status === 'ORDERED' || order.status === 'COLLECTED' || order.status === 'IN_PROGRESS') && (
                            <button type="button" className="btn btn-sm" onClick={() => startRecordResult(order.id)}>
                              Record result
                            </button>
                          )}
                          {order.status === 'RESULT_ENTERED' && (
                            <button type="button" className="btn btn-sm" disabled={isBusy} onClick={() => handleVerify(order)}>
                              Verify
                            </button>
                          )}
                          {order.status === 'VERIFIED' && (
                            <button type="button" className="btn btn-sm" disabled={isBusy} onClick={() => handleRelease(order)}>
                              Release
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  )}
                </tr>

                {collectTargetId === order.id && (
                  <tr>
                    <td colSpan={canRecordResults ? 8 : 7}>
                      <div className="doctor-form-grid">
                        <label className="inline-label">
                          Sample type
                          <input
                            type="text"
                            value={sampleType}
                            placeholder="e.g. Blood, Urine, Serum"
                            onChange={(e) => setSampleType(e.target.value)}
                          />
                        </label>
                        <label className="inline-label">
                          Notes
                          <input type="text" value={sampleNotes} onChange={(e) => setSampleNotes(e.target.value)} />
                        </label>
                      </div>
                      <div className="doctor-quick-actions">
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={collectSaving || !sampleType.trim()}
                          onClick={() => handleCollectSample(order)}
                        >
                          {collectSaving ? 'Saving…' : 'Save collection'}
                        </button>
                        <button type="button" className="btn-secondary btn btn-sm" onClick={cancelCollectSample}>
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                )}

                {resultTargetId === order.id && (
                  <tr>
                    <td colSpan={canRecordResults ? 8 : 7}>
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
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
