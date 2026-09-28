import { Fragment, useEffect, useState } from 'react'
import {
  ApiError,
  createMedication,
  createPharmacyStock,
  dispensePrescriptionItem,
  getPharmacyQueue,
  getPharmacyStock,
  listMedications,
  setMedicationActive,
  updateMedication,
} from '../api'
import type {
  Medication,
  MedicationInput,
  PharmacyQueueEntry,
  PharmacyStockBatch,
  PharmacyStockInput,
  PrescriptionItem,
} from '../types'
import { formatDateTime } from '../format'
import MedicationPicker from './MedicationPicker'

type PharmacyTab = 'queue' | 'stock' | 'medications'

const BLANK_STOCK: PharmacyStockInput = {
  medicine_name: '',
  batch_number: '',
  expiry_date: '',
  quantity_on_hand: 0,
  unit_price: 0,
  medication_id: null,
}

const BLANK_MEDICATION: MedicationInput = {
  generic_name: '',
  brand_name: '',
  strength: '',
  dosage_form: '',
  default_route: '',
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

  const [medications, setMedications] = useState<Medication[]>([])
  const [medicationsLoading, setMedicationsLoading] = useState(false)
  const [medicationsError, setMedicationsError] = useState<string | null>(null)
  const [medicationSearch, setMedicationSearch] = useState('')
  const [medicationForm, setMedicationForm] = useState<MedicationInput>(BLANK_MEDICATION)
  const [savingMedication, setSavingMedication] = useState(false)
  const [editingMedicationId, setEditingMedicationId] = useState<number | null>(null)

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

  function loadMedications(search?: string) {
    setMedicationsLoading(true)
    setMedicationsError(null)
    listMedications(search, true)
      .then(setMedications)
      .catch((err) => setMedicationsError(err instanceof ApiError ? err.message : 'Could not load medications'))
      .finally(() => setMedicationsLoading(false))
  }

  useEffect(() => {
    loadQueue()
  }, [])

  useEffect(() => {
    if (tab === 'stock') loadStock()
    if (tab === 'medications') loadMedications()
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

  function startEditMedication(med: Medication) {
    setEditingMedicationId(med.id)
    setMedicationForm({
      generic_name: med.generic_name,
      brand_name: med.brand_name ?? '',
      strength: med.strength ?? '',
      dosage_form: med.dosage_form ?? '',
      default_route: med.default_route ?? '',
    })
  }

  function cancelEditMedication() {
    setEditingMedicationId(null)
    setMedicationForm(BLANK_MEDICATION)
  }

  async function handleSaveMedication() {
    setSavingMedication(true)
    setMedicationsError(null)
    // Blank optional fields are sent as null, not '' -- an empty string
    // strength/brand would otherwise create spurious distinct-looking
    // duplicates against a genuinely-unset row.
    const payload: MedicationInput = {
      generic_name: medicationForm.generic_name.trim(),
      brand_name: medicationForm.brand_name?.trim() || null,
      strength: medicationForm.strength?.trim() || null,
      dosage_form: medicationForm.dosage_form?.trim() || null,
      default_route: medicationForm.default_route?.trim() || null,
    }
    try {
      const saved = editingMedicationId
        ? await updateMedication(editingMedicationId, payload)
        : await createMedication(payload)
      setMedications((prev) => {
        const others = prev.filter((m) => m.id !== saved.id)
        return [saved, ...others].sort((a, b) => a.generic_name.localeCompare(b.generic_name))
      })
      cancelEditMedication()
    } catch (err) {
      setMedicationsError(
        err instanceof ApiError ? err.message : 'Could not save this medication',
      )
    } finally {
      setSavingMedication(false)
    }
  }

  async function handleToggleMedicationActive(med: Medication) {
    setMedicationsError(null)
    try {
      const updated = await setMedicationActive(med.id, !med.active)
      setMedications((prev) => prev.map((m) => (m.id === updated.id ? updated : m)))
    } catch (err) {
      setMedicationsError(
        err instanceof ApiError ? err.message : 'Could not update this medication',
      )
    }
  }

  function startDispense(item: PrescriptionItem) {
    setDispenseTargetId(item.id)
    setDispenseForm(blankDispenseForm(item.quantity - item.quantity_dispensed))
    setDispenseError(null)
    // Phase 5: prefer the exact medication_id lookup when this item has
    // one -- falls back to the pre-existing fuzzy medicine_name search
    // for anything not yet linked to the Medication Master.
    getPharmacyStock(item.medicine_name, item.medication_id ?? undefined)
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
        <button
          type="button"
          className={tab === 'medications' ? 'tab active' : 'tab'}
          onClick={() => setTab('medications')}
        >
          Medications
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
                <div className="doctor-form-full">
                  <MedicationPicker
                    onSelect={(med) =>
                      setStockForm({
                        ...stockForm,
                        medicine_name: med.display_name,
                        medication_id: med.id,
                      })
                    }
                  />
                </div>
                <label className="inline-label">
                  Medicine
                  <input
                    type="text"
                    value={stockForm.medicine_name}
                    onChange={(e) =>
                      setStockForm({ ...stockForm, medicine_name: e.target.value, medication_id: null })
                    }
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

      {tab === 'medications' && (
        <>
          {medicationsError && <p className="error">{medicationsError}</p>}

          <div className="detail-section">
            <label className="inline-label">
              Search
              <input
                type="text"
                value={medicationSearch}
                placeholder="Search by generic name, brand, or strength…"
                onChange={(e) => {
                  setMedicationSearch(e.target.value)
                  loadMedications(e.target.value)
                }}
              />
            </label>
          </div>

          {canManageStock && (
            <div className="detail-section">
              <h4>{editingMedicationId ? 'Edit medication' : 'Add medication'}</h4>
              <div className="doctor-form-grid">
                <label className="inline-label">
                  Generic name
                  <input
                    type="text"
                    value={medicationForm.generic_name}
                    onChange={(e) => setMedicationForm({ ...medicationForm, generic_name: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Brand name
                  <input
                    type="text"
                    value={medicationForm.brand_name ?? ''}
                    onChange={(e) => setMedicationForm({ ...medicationForm, brand_name: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Strength
                  <input
                    type="text"
                    value={medicationForm.strength ?? ''}
                    onChange={(e) => setMedicationForm({ ...medicationForm, strength: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Dosage form
                  <input
                    type="text"
                    value={medicationForm.dosage_form ?? ''}
                    onChange={(e) => setMedicationForm({ ...medicationForm, dosage_form: e.target.value })}
                  />
                </label>
                <label className="inline-label">
                  Default route
                  <input
                    type="text"
                    value={medicationForm.default_route ?? ''}
                    onChange={(e) => setMedicationForm({ ...medicationForm, default_route: e.target.value })}
                  />
                </label>
                <div className="doctor-form-full doctor-quick-actions">
                  <button
                    type="button"
                    className="btn"
                    disabled={savingMedication || !medicationForm.generic_name.trim()}
                    onClick={handleSaveMedication}
                  >
                    {savingMedication ? 'Saving…' : editingMedicationId ? 'Save changes' : 'Add medication'}
                  </button>
                  {editingMedicationId && (
                    <button type="button" className="btn-secondary btn" onClick={cancelEditMedication}>
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {medicationsLoading && (
            <div className="state-block">
              <span className="spinner" aria-hidden="true" />
              Loading…
            </div>
          )}
          {!medicationsLoading && medications.length === 0 && (
            <p className="muted">No medications match this search.</p>
          )}
          {!medicationsLoading && medications.length > 0 && (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Medication</th>
                  <th>Status</th>
                  {canManageStock && <th>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {medications.map((med) => (
                  <tr key={med.id}>
                    <td>{med.display_name}</td>
                    <td>
                      <span className={`pill ${med.active ? 'status-active' : 'status-inactive'}`}>
                        {med.active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    {canManageStock && (
                      <td>
                        <div className="doctor-quick-actions">
                          <button type="button" className="btn btn-sm" onClick={() => startEditMedication(med)}>
                            Edit
                          </button>
                          <button
                            type="button"
                            className="btn-secondary btn btn-sm"
                            onClick={() => handleToggleMedicationActive(med)}
                          >
                            {med.active ? 'Deactivate' : 'Activate'}
                          </button>
                        </div>
                      </td>
                    )}
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
