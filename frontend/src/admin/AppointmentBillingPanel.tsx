import { useEffect, useState } from 'react'
import {
  ApiError,
  addBillCharge,
  getBill,
  getUnbilledSources,
  listPackages,
  recordBillPayment,
  updateBillTerms,
  voidBill,
  voidBillCharge,
  voidBillPayment,
} from '../api'
import type {
  BillChargeInput,
  BillPaymentInput,
  BillSummary,
  BillType,
  ChargeSourceType,
  Package,
  UnbilledSources,
} from '../types'
import { formatDateTime } from '../format'

const BILL_TYPE_OPTIONS: { value: BillType; label: string }[] = [
  { value: 'CASH', label: 'Cash' },
  { value: 'SELF_PAY', label: 'Self-pay' },
  { value: 'CORPORATE', label: 'Corporate' },
  { value: 'INSURANCE', label: 'Insurance' },
  { value: 'TPA', label: 'TPA' },
  { value: 'GOVERNMENT_SCHEME', label: 'Government scheme' },
]

const BLANK_CHARGE: BillChargeInput = {
  description: '',
  amount: 0,
  source_type: 'OTHER',
}

const BLANK_PAYMENT: BillPaymentInput = {
  amount: 0,
  method: 'CASH',
}

const SOURCE_TYPE_OPTIONS: ChargeSourceType[] = [
  'CONSULTATION',
  'LAB',
  'RADIOLOGY',
  'PROCEDURE',
  'SERVICE',
  'PHARMACY',
  'OTHER',
]

// OPD/HIMS master spec Phase 9 -- the Billing tab of ConsultationWorkspace,
// for the NEW encounter-scoped invoice/charge/payment model
// (app/api/billing.py, /appointments/{id}/bill...). Named
// AppointmentBillingPanel, not BillingPanel, because that name is already
// taken by the dashboard's billing reconciliation page (admin/
// BillingPanel.tsx, GET /dashboard/billing) -- an unrelated, older
// component this one must never collide with.
//
// Self-contained and lazily loaded, same pattern as PrescriptionPanel.tsx
// (Phase 8): fetches its own data when this tab is opened rather than
// threading through the parent's shared initial load. Unlike every other
// tab in that workspace, this one is NOT gated on the appointment being
// CHECKED_IN -- billing_services.py's module docstring explains why (it's
// an administrative/financial function, not clinical documentation), so
// this panel is reachable even when ConsultationWorkspace's own
// notCheckedIn state is true.
export default function AppointmentBillingPanel({
  appointmentId,
  isAdmin,
}: {
  appointmentId: number
  isAdmin: boolean
}) {
  const [bill, setBill] = useState<BillSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [unbilled, setUnbilled] = useState<UnbilledSources | null>(null)

  const [chargeForm, setChargeForm] = useState<BillChargeInput>(BLANK_CHARGE)
  const [addingCharge, setAddingCharge] = useState(false)
  const [chargeError, setChargeError] = useState<string | null>(null)
  const [voidingChargeId, setVoidingChargeId] = useState<number | null>(null)

  const [paymentForm, setPaymentForm] = useState<BillPaymentInput>(BLANK_PAYMENT)
  const [recordingPayment, setRecordingPayment] = useState(false)
  const [paymentError, setPaymentError] = useState<string | null>(null)
  const [voidingPaymentId, setVoidingPaymentId] = useState<number | null>(null)

  const [showTermsForm, setShowTermsForm] = useState(false)
  const [discountAmount, setDiscountAmount] = useState('')
  const [discountReason, setDiscountReason] = useState('')
  const [taxRate, setTaxRate] = useState('')
  const [billType, setBillType] = useState<BillType | ''>('')
  const [savingTerms, setSavingTerms] = useState(false)
  const [termsError, setTermsError] = useState<string | null>(null)

  const [packages, setPackages] = useState<Package[]>([])
  const [selectedPackageId, setSelectedPackageId] = useState<number | ''>('')

  const [voidingBill, setVoidingBill] = useState(false)
  const [voidBillError, setVoidBillError] = useState<string | null>(null)

  function loadBill() {
    setLoading(true)
    setLoadError(null)
    return getBill(appointmentId)
      .then(setBill)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Could not load the bill'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    let cancelledEffect = false
    setLoading(true)
    setLoadError(null)

    getBill(appointmentId)
      .then((b) => {
        if (!cancelledEffect) setBill(b)
      })
      .catch((err) => {
        if (!cancelledEffect) setLoadError(err instanceof ApiError ? err.message : 'Could not load the bill')
      })
      .finally(() => {
        if (!cancelledEffect) setLoading(false)
      })

    getUnbilledSources(appointmentId)
      .then((u) => {
        if (!cancelledEffect) setUnbilled(u)
      })
      .catch(() => {
        if (!cancelledEffect) setUnbilled(null)
      })

    listPackages()
      .then((list) => {
        if (!cancelledEffect) setPackages(list)
      })
      .catch(() => {
        if (!cancelledEffect) setPackages([])
      })

    return () => {
      cancelledEffect = true
    }
  }, [appointmentId])

  async function handleAddCharge(overrides?: Partial<BillChargeInput>) {
    const payload = { ...chargeForm, ...overrides }
    setAddingCharge(true)
    setChargeError(null)
    try {
      setBill(await addBillCharge(appointmentId, payload))
      setChargeForm(BLANK_CHARGE)
      await getUnbilledSources(appointmentId).then(setUnbilled).catch(() => null)
    } catch (err) {
      setChargeError(err instanceof ApiError ? err.message : 'Could not add this charge')
    } finally {
      setAddingCharge(false)
    }
  }

  async function handleVoidCharge(chargeId: number) {
    const reason = window.prompt('Reason for voiding this charge:')
    if (!reason || !reason.trim()) return
    setVoidingChargeId(chargeId)
    setChargeError(null)
    try {
      setBill(await voidBillCharge(appointmentId, chargeId, reason.trim()))
    } catch (err) {
      setChargeError(err instanceof ApiError ? err.message : 'Could not void this charge')
    } finally {
      setVoidingChargeId(null)
    }
  }

  async function handleRecordPayment() {
    setRecordingPayment(true)
    setPaymentError(null)
    try {
      setBill(
        await recordBillPayment(appointmentId, {
          ...paymentForm,
          transaction_id: paymentForm.transaction_id?.trim() || undefined,
        }),
      )
      setPaymentForm(BLANK_PAYMENT)
    } catch (err) {
      setPaymentError(err instanceof ApiError ? err.message : 'Could not record this payment')
    } finally {
      setRecordingPayment(false)
    }
  }

  async function handleSaveTerms() {
    setSavingTerms(true)
    setTermsError(null)
    try {
      setBill(
        await updateBillTerms(appointmentId, {
          discount_amount: discountAmount.trim() ? Number(discountAmount) : undefined,
          discount_reason: discountReason.trim() || undefined,
          tax_rate: taxRate.trim() ? Number(taxRate) : undefined,
          bill_type: billType || undefined,
        }),
      )
      setShowTermsForm(false)
      setDiscountAmount('')
      setDiscountReason('')
      setTaxRate('')
      setBillType('')
    } catch (err) {
      setTermsError(err instanceof ApiError ? err.message : 'Could not update discount/tax')
    } finally {
      setSavingTerms(false)
    }
  }

  async function handleVoidBill() {
    const reason = window.prompt('Reason for voiding this entire bill:')
    if (!reason || !reason.trim()) return
    setVoidingBill(true)
    setVoidBillError(null)
    try {
      setBill(await voidBill(appointmentId, reason.trim()))
    } catch (err) {
      setVoidBillError(err instanceof ApiError ? err.message : 'Could not void this bill')
    } finally {
      setVoidingBill(false)
    }
  }

  async function handleVoidPayment(paymentId: number) {
    const reason = window.prompt('Reason for voiding this payment:')
    if (!reason || !reason.trim()) return
    setVoidingPaymentId(paymentId)
    setPaymentError(null)
    try {
      setBill(await voidBillPayment(appointmentId, paymentId, reason.trim()))
    } catch (err) {
      setPaymentError(err instanceof ApiError ? err.message : 'Could not void this payment')
    } finally {
      setVoidingPaymentId(null)
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
    return (
      <div>
        <p className="error">{loadError}</p>
        <button type="button" className="btn-secondary btn btn-sm" onClick={loadBill}>
          Retry
        </button>
      </div>
    )
  }

  if (!bill) return null

  const isVoid = bill.status === 'VOID'
  const activeCharges = bill.charges.filter((c) => c.status === 'ACTIVE')
  const voidedCharges = bill.charges.filter((c) => c.status === 'VOIDED')
  const hasUnbilled = unbilled && (unbilled.orders.length > 0 || unbilled.dispenses.length > 0)

  return (
    <div className="detail-section">
      <div className="patient-context-meta">
        <span>Bill {bill.invoice_number}</span>
        <span className={`pill status-${bill.payment_status.toLowerCase()}`}>
          {bill.payment_status.replace('_', ' ')}
        </span>
        <span className="pill status-pending">
          {BILL_TYPE_OPTIONS.find((o) => o.value === bill.bill_type)?.label ?? bill.bill_type}
        </span>
        {isVoid && <span className="pill status-cancelled">VOID</span>}
      </div>
      {isVoid && bill.void_reason && <p className="muted">Voided: {bill.void_reason}</p>}

      <table className="data-table">
        <tbody>
          <tr>
            <td>Gross</td>
            <td>₹{bill.gross_amount.toFixed(2)}</td>
          </tr>
          <tr>
            <td>Discount{bill.discount_reason ? ` (${bill.discount_reason})` : ''}</td>
            <td>−₹{bill.discount_amount.toFixed(2)}</td>
          </tr>
          <tr>
            <td>Tax ({bill.tax_rate}%)</td>
            <td>₹{bill.tax_amount.toFixed(2)}</td>
          </tr>
          <tr>
            <td>
              <strong>Net</strong>
            </td>
            <td>
              <strong>₹{bill.net_amount.toFixed(2)}</strong>
            </td>
          </tr>
          <tr>
            <td>Paid</td>
            <td>₹{bill.paid_amount.toFixed(2)}</td>
          </tr>
          <tr>
            <td>
              <strong>Balance</strong>
            </td>
            <td>
              <strong>₹{bill.balance.toFixed(2)}</strong>
            </td>
          </tr>
        </tbody>
      </table>

      {isAdmin && !isVoid && (
        <div className="doctor-quick-actions">
          {showTermsForm ? (
            <div className="doctor-form-grid">
              {termsError && <p className="error">{termsError}</p>}
              <label className="inline-label">
                Discount amount
                <input
                  type="number"
                  min={0}
                  step="0.01"
                  value={discountAmount}
                  onChange={(e) => setDiscountAmount(e.target.value)}
                  placeholder={String(bill.discount_amount)}
                />
              </label>
              <label className="inline-label">
                Discount reason
                <input
                  type="text"
                  value={discountReason}
                  onChange={(e) => setDiscountReason(e.target.value)}
                  placeholder={bill.discount_reason ?? ''}
                />
              </label>
              <label className="inline-label">
                Tax rate (%)
                <input
                  type="number"
                  min={0}
                  max={100}
                  step="0.01"
                  value={taxRate}
                  onChange={(e) => setTaxRate(e.target.value)}
                  placeholder={String(bill.tax_rate)}
                />
              </label>
              <label className="inline-label">
                Bill type
                <select value={billType} onChange={(e) => setBillType(e.target.value as BillType)}>
                  <option value="">{BILL_TYPE_OPTIONS.find((o) => o.value === bill.bill_type)?.label}</option>
                  {BILL_TYPE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="doctor-form-full">
                <button type="button" className="btn btn-sm" disabled={savingTerms} onClick={handleSaveTerms}>
                  {savingTerms ? 'Saving…' : 'Save'}
                </button>
                <button
                  type="button"
                  className="btn-secondary btn btn-sm"
                  onClick={() => setShowTermsForm(false)}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button type="button" className="btn-secondary btn btn-sm" onClick={() => setShowTermsForm(true)}>
              Edit discount / tax
            </button>
          )}
          {bill.payments.filter((p) => p.status === 'COMPLETED').length === 0 && (
            <button type="button" className="btn-danger btn btn-sm" disabled={voidingBill} onClick={handleVoidBill}>
              {voidingBill ? 'Voiding…' : 'Void bill'}
            </button>
          )}
        </div>
      )}
      {voidBillError && <p className="error">{voidBillError}</p>}

      <h4>Charges</h4>
      {chargeError && <p className="error">{chargeError}</p>}
      {activeCharges.length === 0 && <p className="muted">No charges yet.</p>}
      {activeCharges.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Description</th>
              <th>Type</th>
              <th>Amount</th>
              {isAdmin && !isVoid && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {activeCharges.map((c) => (
              <tr key={c.id}>
                <td>{c.description}</td>
                <td>{c.source_type}</td>
                <td>₹{c.amount.toFixed(2)}</td>
                {isAdmin && !isVoid && (
                  <td>
                    <button
                      type="button"
                      className="btn-danger btn btn-sm"
                      disabled={voidingChargeId === c.id}
                      onClick={() => handleVoidCharge(c.id)}
                    >
                      {voidingChargeId === c.id ? 'Voiding…' : 'Void'}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {voidedCharges.length > 0 && (
        <p className="muted">
          {voidedCharges.length} voided charge{voidedCharges.length === 1 ? '' : 's'} not shown in the total.
        </p>
      )}

      {isAdmin && !isVoid && (
        <div className="doctor-form-grid">
          <label className="inline-label doctor-form-full">
            Description *
            <input
              type="text"
              value={chargeForm.description}
              onChange={(e) => setChargeForm({ ...chargeForm, description: e.target.value })}
              placeholder="e.g. Consultation fee"
            />
          </label>
          <label className="inline-label">
            Amount *
            <input
              type="number"
              min={0.01}
              step="0.01"
              value={chargeForm.amount || ''}
              onChange={(e) => setChargeForm({ ...chargeForm, amount: Number(e.target.value) })}
            />
          </label>
          <label className="inline-label">
            Type
            <select
              value={chargeForm.source_type}
              onChange={(e) => setChargeForm({ ...chargeForm, source_type: e.target.value as ChargeSourceType })}
            >
              {SOURCE_TYPE_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <div className="doctor-form-full">
            <button
              type="button"
              className="btn-secondary btn"
              disabled={addingCharge || !chargeForm.description.trim() || !chargeForm.amount}
              onClick={() => handleAddCharge()}
            >
              {addingCharge ? 'Adding…' : '+ Add charge'}
            </button>
          </div>
        </div>
      )}

      {isAdmin && !isVoid && packages.length > 0 && (
        <div className="doctor-quick-actions">
          <label className="inline-label">
            Bill a package
            <select
              value={selectedPackageId}
              onChange={(e) => setSelectedPackageId(e.target.value ? Number(e.target.value) : '')}
            >
              <option value="">Select a package…</option>
              {packages.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} — ₹{p.price.toFixed(2)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn-secondary btn btn-sm"
            disabled={addingCharge || !selectedPackageId}
            onClick={() => {
              const pkg = packages.find((p) => p.id === selectedPackageId)
              if (!pkg) return
              handleAddCharge({
                description: pkg.name,
                source_type: 'PACKAGE',
                source_package_id: pkg.id,
                amount: pkg.price,
              })
              setSelectedPackageId('')
            }}
          >
            Bill this
          </button>
        </div>
      )}

      {isAdmin && !isVoid && hasUnbilled && (
        <>
          <h4>Unbilled from this visit</h4>
          {unbilled!.orders.map((o) => (
            <div key={`order-${o.order_id}`} className="doctor-quick-actions">
              <span>
                {o.order_type}: {o.description}
              </span>
              <button
                type="button"
                className="btn-secondary btn btn-sm"
                disabled={addingCharge}
                onClick={() => {
                  // Orders have no catalog price (no price catalog exists
                  // yet, same "staff-entered amount" stance as every other
                  // charge in this app) -- ask for one rather than silently
                  // sending an amount-less request the backend would 422 on.
                  const entered = window.prompt(`Amount to bill for "${o.description}":`)
                  if (entered === null) return
                  const amount = Number(entered)
                  if (!Number.isFinite(amount) || amount <= 0) {
                    setChargeError('Enter a valid amount greater than 0')
                    return
                  }
                  handleAddCharge({
                    description: o.description,
                    source_type: o.order_type === 'EXTERNAL_REFERRAL' ? 'OTHER' : (o.order_type as ChargeSourceType),
                    source_order_id: o.order_id,
                    amount,
                  })
                }}
              >
                Bill this
              </button>
            </div>
          ))}
          {unbilled!.dispenses.map((d) => (
            <div key={`dispense-${d.dispense_id}`} className="doctor-quick-actions">
              <span>
                {d.medicine_name} × {d.quantity} — ₹{d.amount.toFixed(2)}
              </span>
              <button
                type="button"
                className="btn-secondary btn btn-sm"
                disabled={addingCharge}
                onClick={() =>
                  handleAddCharge({
                    description: `${d.medicine_name} × ${d.quantity}`,
                    source_type: 'PHARMACY',
                    source_dispense_id: d.dispense_id,
                    amount: d.amount,
                  })
                }
              >
                Bill this
              </button>
            </div>
          ))}
        </>
      )}

      <h4>Payments</h4>
      {paymentError && <p className="error">{paymentError}</p>}
      {bill.payments.length === 0 && <p className="muted">No payments recorded yet.</p>}
      {bill.payments.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Receipt</th>
              <th>Method</th>
              <th>Amount</th>
              <th>Refunded</th>
              <th>Status</th>
              <th>Recorded</th>
              {isAdmin && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {bill.payments.map((p) => (
              <tr key={p.id}>
                <td>{p.receipt_number}</td>
                <td>{p.method}</td>
                <td>₹{p.amount.toFixed(2)}</td>
                <td>{p.refunded_amount > 0 ? `₹${p.refunded_amount.toFixed(2)}` : '—'}</td>
                <td>
                  <span className={`pill status-${p.status.toLowerCase()}`}>{p.status}</span>
                </td>
                <td>{formatDateTime(p.recorded_at)}</td>
                {isAdmin && (
                  <td>
                    {p.status === 'COMPLETED' && (
                      <button
                        type="button"
                        className="btn-danger btn btn-sm"
                        disabled={voidingPaymentId === p.id}
                        onClick={() => handleVoidPayment(p.id)}
                      >
                        {voidingPaymentId === p.id ? 'Voiding…' : 'Void'}
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!isVoid && bill.balance > 0 && (
        <div className="doctor-form-grid">
          <label className="inline-label">
            Amount *
            <input
              type="number"
              min={0.01}
              max={bill.balance}
              step="0.01"
              value={paymentForm.amount || ''}
              onChange={(e) => setPaymentForm({ ...paymentForm, amount: Number(e.target.value) })}
            />
          </label>
          <label className="inline-label">
            Method
            <select
              value={paymentForm.method}
              onChange={(e) => setPaymentForm({ ...paymentForm, method: e.target.value as BillPaymentInput['method'] })}
            >
              <option value="CASH">CASH</option>
              <option value="UPI">UPI</option>
              <option value="CARD">CARD</option>
              <option value="BANK_TRANSFER">BANK_TRANSFER</option>
              <option value="INSURANCE">INSURANCE</option>
              <option value="OTHER">OTHER</option>
            </select>
          </label>
          <label className="inline-label">
            Transaction ID
            <input
              type="text"
              value={paymentForm.transaction_id ?? ''}
              onChange={(e) => setPaymentForm({ ...paymentForm, transaction_id: e.target.value })}
            />
          </label>
          <div className="doctor-form-full">
            <button
              type="button"
              className="btn"
              disabled={recordingPayment || !paymentForm.amount || paymentForm.amount > bill.balance}
              onClick={handleRecordPayment}
            >
              {recordingPayment ? 'Recording…' : 'Record payment'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
