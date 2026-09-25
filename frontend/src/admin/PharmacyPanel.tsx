import { Fragment, useEffect, useState } from 'react'
import { ApiError, createPharmacyStock, dispensePrescriptionItem, getPharmacyQueue, getPharmacyStock } from '../api'
import type { PharmacyQueueEntry, PharmacyStockBatch, PharmacyStockInput, PrescriptionItem } from '../types'
import { formatDateTime } from '../format'

type PharmacyTab = 'queue' | 'stock'

const BLANK_STOCK: PharmacyStockInput = {
  medicine_name: '',
  batch_number: '',
  expiry_date: '',
  quantity_on_hand: 0,
  unit_price: 0,
}

type DispenseForm = { quantity: number; pharmacy_stock_id: number | ''; unit_price: string }

function blankDispenseForm(remaining: number): DispenseForm {
  return { quantity: remaining, pharmacy_stock_id: '', unit_price: '' }
}

// OPD/HIMS master spec Phase 8 -- the pharmacy's own cross-patient
// workspace: a work queue of prescriptions pharmacy hasn't finished
// dispensing yet, and (ADMIN only, for adding a new batch) the stock
// catalog. Reached from the sidebar directly (AdminApp.tsx), unlike
// Queue/Consultation which are only reached as actions -- pharmacy
// staff start their day here, the same way Billing is its own direct
// destination. Dispensing itself stays open to any authenticated staff
// session (bare auth, same as vitals/orders/prescriptions); only stock
// management (canManageStock below) is real RBAC -- server-enforced by
// pharmacy.manage_stock (migrations/0043_role_based_access.sql),
// granted to ADMIN and PHARMACIST.
export default function PharmacyPanel({ canManageStock }: { canManageStock: boolean }) {
  const [tab, setTab] = useState<PharmacyTab>('queue')
  const [queue, setQueue] = useState<PharmacyQueueEntry[]>([])
  const [queueLoading, setQueueLoading] = useState(true)
  const [queueError, setQueueError] = useState<string | null>(null)

  const [stock, setStock] = useState<PharmacyStockBatch[]>([])
  const [stockLoading, setStockLoading] = useState(false)
  const [stockError, setStockError] = useState<string | null>(null)
  const [stockForm, setStockForm] = useState<PharmacyStockInput>(BLANK_STOCK)
  const [addingStock, setAddingStock] = useState(false)

  const [dispenseTargetId, setDispenseTargetId] = useState<number | null>(null)
  const [dispenseForm, setDispenseForm] = useState<DispenseForm>(blankDispenseForm(0))
  const [dispenseStock, setDispenseStock] = useState<PharmacyStockBatch[]>([])
  const [dispensing, setDispensing] = useState(false)
  const [dispenseError, setDispenseError] = useState<string | null>(null)

  function loadQueue() {
    setQueueLoading(true)
    setQueueError(null)
    getPharmacyQueue()
      .then(setQueue)
      .catch((err) => setQueueError(err instanceof ApiError ? err.message : 'Could not load the pharmacy queue'))
      .finally(() => setQueueLoading(false))
  }

  function loadStock() {
    setStockLoading(true)
    setStockError(null)
    getPharmacyStock()
      .then(setStock)
      .catch((err) => setStockError(err instanceof ApiError ? err.message : 'Could not load stock'))
      .finally(() => setStockLoading(false))
  }

  useEffect(() => {
    loadQueue()
  }, [])

  useEffect(() => {
    if (tab === 'stock') loadStock()
  }, [tab])

  async function handleAddStock() {
    setAddingStock(true)
    setStockError(null)
    try {
      const created = await createPharmacyStock(stockForm)
      setStock((prev) => [created, ...prev])
      setStockForm(BLANK_STOCK)
    } catch (err) {
      setStockError(err instanceof ApiError ? err.message : 'Could not add this stock batch')
    } finally {
      setAddingStock(false)
    }
  }

  function startDispense(item: PrescriptionItem) {
    setDispenseTargetId(item.id)
    setDispenseForm(blankDispenseForm(item.quantity - item.quantity_dispensed))
    setDispenseError(null)
    getPharmacyStock(item.medicine_name)
      .then((batches) => setDispenseStock(batches.filter((b) => b.quantity_on_hand > 0)))
      .catch(() => setDispenseStock([]))
  }

  function cancelDispense() {
    setDispenseTargetId(null)
    setDispenseStock([])
  }

  async function handleDispense(itemId: number) {
    setDispensing(true)
    setDispenseError(null)
    try {
      await dispensePrescriptionItem(itemId, {
        quantity: dispenseForm.quantity,
        pharmacy_stock_id: dispenseForm.pharmacy_stock_id === '' ? undefined : dispenseForm.pharmacy_stock_id,
        unit_price: dispenseForm.unit_price.trim() ? Number(dispenseForm.unit_price) : undefined,
      })
      setDispenseTargetId(null)
      loadQueue()
    } catch (err) {
      setDispenseError(err instanceof ApiError ? err.message : 'Could not record this dispense')
    } finally {
      setDispensing(false)
    }
  }

  return (
    <section>
      <div className="admin-content-header">
        <div>
          <h2>Pharmacy</h2>
          <p className="muted">Prescriptions waiting to be dispensed, and the stock catalog.</p>
        </div>
      </div>

      <div className="tabs">
        <button type="button" className={tab === 'queue' ? 'tab active' : 'tab'} onClick={() => setTab('queue')}>
          Queue{queue.length > 0 ? ` (${queue.length})` : ''}
        </button>
        <button type="button" className={tab === 'stock' ? 'tab active' : 'tab'} onClick={() => setTab('stock')}>
          Stock
        </button>
      </div>

      {tab === 'queue' && (
        <>
          {queueError && <p className="error">{queueError}</p>}
          {queueLoading && (
            <div className="state-block">
              <span className="spinner" aria-hidden="true" />
              Loading…
            </div>
          )}
          {!queueLoading && queue.length === 0 && (
            <p className="muted">No pending prescriptions. Prescriptions sent by a doctor will appear here.</p>
          )}
          {!queueLoading &&
            queue.map((entry) => (
              <div key={entry.prescription_id} className="detail-section">
                <h4>
                  {entry.patient_name} <span className="muted">({entry.patient_uhid})</span>
                </h4>
                <p className="muted">
                  {entry.doctor_name} · sent {formatDateTime(entry.prescribed_at)}
                </p>
                {dispenseError && dispenseTargetId !== null && (
                  <p className="error">{dispenseError}</p>
                )}
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Medicine</th>
                      <th>Dosage</th>
                      <th>Prescribed Qty</th>
                      <th>Dispensed</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entry.items.map((item) => (
                      <Fragment key={item.id}>
                        <tr>
                          <td>{item.medicine_name}</td>
                          <td>{item.dosage || '—'}</td>
                          <td>{item.quantity}</td>
                          <td>{item.quantity_dispensed}</td>
                          <td>
                            <span className={`pill status-${item.dispense_status.toLowerCase()}`}>
                              {item.dispense_status.replace('_', ' ')}
                            </span>
                          </td>
                          <td>
                            {item.dispense_status !== 'DISPENSED' &&
                              (dispenseTargetId === item.id ? null : (
                                <button type="button" className="btn btn-sm" onClick={() => startDispense(item)}>
                                  Dispense
                                </button>
                              ))}
                          </td>
                        </tr>
                        {dispenseTargetId === item.id && (
                          <tr>
                            <td colSpan={6}>
                              <div className="doctor-form-grid">
                                <label className="inline-label">
                                  Dispense qty
                                  <input
                                    type="number"
                                    min={1}
                                    max={item.quantity - item.quantity_dispensed}
                                    value={dispenseForm.quantity}
                                    onChange={(e) =>
                                      setDispenseForm({ ...dispenseForm, quantity: Number(e.target.value) })
                                    }
                                  />
                                </label>
                                <label className="inline-label">
                                  Stock batch
                                  <select
                                    value={dispenseForm.pharmacy_stock_id}
                                    onChange={(e) =>
                                      setDispenseForm({
                                        ...dispenseForm,
                                        pharmacy_stock_id: e.target.value ? Number(e.target.value) : '',
                                      })
                                    }
                                  >
                                    <option value="">No stock batch (untracked)</option>
                                    {dispenseStock.map((b) => (
                                      <option key={b.id} value={b.id}>
                                        {b.batch_number} — {b.quantity_on_hand} on hand, exp {b.expiry_date}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                                {dispenseForm.pharmacy_stock_id === '' && (
                                  <label className="inline-label">
                                    Unit price (optional)
                                    <input
                                      type="number"
                                      min={0}
                                      step="0.01"
                                      value={dispenseForm.unit_price}
                                      onChange={(e) =>
                                        setDispenseForm({ ...dispenseForm, unit_price: e.target.value })
                                      }
                                    />
                                  </label>
                                )}
                              </div>
                              <div className="doctor-quick-actions">
                                <button
                                  type="button"
                                  className="btn btn-sm"
                                  disabled={dispensing || !dispenseForm.quantity}
                                  onClick={() => handleDispense(item.id)}
                                >
                                  {dispensing ? 'Dispensing…' : 'Confirm dispense'}
                                </button>
                                <button type="button" className="btn-secondary btn btn-sm" onClick={cancelDispense}>
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
              </div>
            ))}
        </>
      )}

      {tab === 'stock' && (
        <>
          {stockError && <p className="error">{stockError}</p>}
          {canManageStock && (
            <div className="detail-section">
              <h4>Add stock batch</h4>
              <div className="doctor-form-grid">
                <label className="inline-label">
                  Medicine
                  <input
                    type="text"
                    value={stockForm.medicine_name}
                    onChange={(e) => setStockForm({ ...stockForm, medicine_name: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Batch number
                  <input
                    type="text"
                    value={stockForm.batch_number}
                    onChange={(e) => setStockForm({ ...stockForm, batch_number: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Expiry date
                  <input
                    type="date"
                    value={stockForm.expiry_date}
                    onChange={(e) => setStockForm({ ...stockForm, expiry_date: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Quantity
                  <input
                    type="number"
                    min={0}
                    value={stockForm.quantity_on_hand}
                    onChange={(e) => setStockForm({ ...stockForm, quantity_on_hand: Number(e.target.value) })}
                  />
                </label>
                <label className="inline-label">
                  Unit price
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    value={stockForm.unit_price}
                    onChange={(e) => setStockForm({ ...stockForm, unit_price: Number(e.target.value) })}
                  />
                </label>
                <div className="doctor-form-full">
                  <button
                    type="button"
                    className="btn"
                    disabled={
                      addingStock ||
                      !stockForm.medicine_name.trim() ||
                      !stockForm.batch_number.trim() ||
                      !stockForm.expiry_date
                    }
                    onClick={handleAddStock}
                  >
                    {addingStock ? 'Adding…' : 'Add batch'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {stockLoading && (
            <div className="state-block">
              <span className="spinner" aria-hidden="true" />
              Loading…
            </div>
          )}
          {!stockLoading && stock.length === 0 && <p className="muted">No stock batches recorded yet.</p>}
          {!stockLoading && stock.length > 0 && (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Medicine</th>
                  <th>Batch</th>
                  <th>Expiry</th>
                  <th>On hand</th>
                  <th>Unit price</th>
                </tr>
              </thead>
              <tbody>
                {stock.map((s) => (
                  <tr key={s.id}>
                    <td>{s.medicine_name}</td>
                    <td>{s.batch_number}</td>
                    <td>{s.expiry_date}</td>
                    <td>{s.quantity_on_hand}</td>
                    <td>₹{s.unit_price}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}
