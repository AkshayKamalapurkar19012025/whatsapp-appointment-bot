import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Buildings, CalendarBlank, CaretDoubleRight, Check, Clock, Coffee, Info, Prohibit, Warning, X } from '@phosphor-icons/react'
import { ApiError, createDoctorSchedule, deleteDoctorSchedule, listAdminAppointments } from '../api'
import type { AdminAppointment, Department } from '../types'
import { formatDate, formatTimeOfDay } from '../format'
import {
  blocksToRowPayloads,
  dateToDayOfWeek,
  enumerateOccurrences,
  shiftDateStr,
  splitGroupForEdit,
  type EditScope,
  type ScheduleBlock,
} from './doctorSchedule'

type Step = 1 | 2 | 3 | 'success'

// Remove Schedule -- a dedicated, deliberately slower workflow for a
// destructive action, opened from Configure Schedule's edit-mode
// "Schedule actions" menu (and its own footer Remove Schedule buttons)
// the same way Duplicate Schedule is: ScheduleGrid.tsx owns the actual
// open/close state, ConfigureScheduleModal just calls back into it.
// Reuses the exact same scope semantics/removal logic Configure
// Schedule's own edit mode already has (EditScope, splitGroupForEdit)
// -- this is a richer confirmation UI around that logic, not a second
// deletion mechanism.
export default function RemoveScheduleModal({
  doctorId,
  departments,
  editingGroup,
  initialDate,
  onClose,
  onSaved,
}: {
  doctorId: number
  departments: Department[]
  editingGroup: ScheduleBlock[]
  initialDate: string
  onClose: () => void
  onSaved: () => void
}) {
  const [step, setStep] = useState<Step>(1)
  const [scope, setScope] = useState<EditScope | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedSummary, setSavedSummary] = useState('')
  const [affectedAppointments, setAffectedAppointments] = useState<AdminAppointment[]>([])

  const groupDay = editingGroup[0]?.day ?? dateToDayOfWeek(initialDate)
  const groupStart = editingGroup[0]?.startDate ?? null
  const groupEnd = editingGroup[0]?.endDate ?? null
  const departmentIdStr = editingGroup[0]?.departmentId ? String(editingGroup[0].departmentId) : ''
  const hasBreaks = editingGroup.some((b) => b.breaks.length > 0)

  function departmentName(id: string): string {
    if (!id) return 'All departments'
    return departments.find((d) => d.id === Number(id))?.name ?? 'All departments'
  }

  const scopeLabel = (s: EditScope): string =>
    s === 'this_date' ? 'This date only' : s === 'this_and_future' ? 'This and future' : 'Entire schedule'

  const scopeDescription = (s: EditScope): string =>
    s === 'this_date'
      ? `Remove the schedule only for ${formatDate(initialDate)}`
      : s === 'this_and_future'
        ? `Remove this schedule from ${formatDate(initialDate)} onward`
        : 'Remove the complete recurring schedule (all dates)'

  // Only a bounded range has a countable number of dates -- an
  // unbounded "this and future"/"entire" (no end date) is described
  // in words instead of a number that would otherwise have to be
  // invented.
  const affectedCount: number | null =
    scope === 'this_date'
      ? 1
      : scope === 'this_and_future'
        ? groupEnd ? enumerateOccurrences([groupDay], initialDate, groupEnd).length : null
        : scope === 'entire'
          ? groupStart && groupEnd ? enumerateOccurrences([groupDay], groupStart, groupEnd).length : null
          : null

  const affectedRangeText: string | null =
    scope === 'this_date'
      ? formatDate(initialDate)
      : scope === 'this_and_future'
        ? `${formatDate(initialDate)} onward${groupEnd ? ` to ${formatDate(groupEnd)}` : ', with no end date'}`
        : scope === 'entire'
          ? `${groupStart ? formatDate(groupStart) : 'the start'} to ${groupEnd ? formatDate(groupEnd) : 'no end date'}`
          : null

  const impactText: string | null =
    scope && affectedRangeText
      ? affectedCount !== null
        ? `${affectedCount} date${affectedCount === 1 ? '' : 's'} affected -- ${affectedRangeText}.`
        : `${affectedRangeText}, with no end date -- the exact number of dates can't be counted.`
      : null

  // A removal that reaches beyond just today's date needs the extra
  // "I understand" acknowledgement -- a simple one-day removal doesn't.
  const requiresAck = scope === 'entire' || scope === 'this_and_future'

  useEffect(() => {
    if (!scope) return
    const from = scope === 'entire' ? groupStart ?? initialDate : initialDate
    const to = scope === 'this_date' ? initialDate : groupEnd ?? shiftDateStr(initialDate, 90)
    listAdminAppointments({ doctor_id: doctorId, date_from: from, date_to: to })
      .then((all) => setAffectedAppointments(all.filter((a) => ['PENDING', 'CONFIRMED', 'CHECKED_IN'].includes(a.status))))
      .catch(() => setAffectedAppointments([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, doctorId, initialDate, groupStart, groupEnd])

  async function handleConfirmRemove() {
    if (!scope) return
    setBusy(true)
    setError(null)
    const finalGroup = splitGroupForEdit(editingGroup, initialDate, scope, null)
    try {
      for (const id of editingGroup.flatMap((b) => b.sourceIds)) {
        await deleteDoctorSchedule(doctorId, id)
      }
      for (const payload of blocksToRowPayloads(finalGroup)) {
        await createDoctorSchedule(doctorId, payload)
      }
      setSavedSummary(affectedRangeText ?? '')
      onSaved()
      setStep('success')
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Could not remove this schedule'
      setError(`${msg} -- check the calendar and retry.`)
    } finally {
      setBusy(false)
    }
  }

  const stepLabels = ['Select Scope', 'Review Impact', 'Confirm Removal']
  const displayStep = typeof step === 'number' ? step : stepLabels.length

  return createPortal(
    <div className="modal-overlay schedule-modal-overlay" onClick={onClose}>
      <div
        className="modal-panel schedule-modal-panel schedule-modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Remove Schedule"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <div className="schedule-modal-header">
          <div className="schedule-modal-header-top">
            <h3 className="appointment-details-heading" style={{ margin: 0 }}>Remove Schedule</h3>
          </div>
          {step !== 'success' && (
            <p className="muted schedule-modal-subtitle">Choose which part of this schedule you want to remove.</p>
          )}
          <div className="schedule-modal-stepper" aria-label={step === 'success' ? 'All steps complete' : `Step ${displayStep} of ${stepLabels.length}`}>
            {stepLabels.map((label, i) => {
              const stepNum = i + 1
              const currentNum = typeof displayStep === 'number' ? displayStep : stepLabels.length
              const state = step === 'success' ? 'done' : stepNum < currentNum ? 'done' : stepNum === currentNum ? 'current' : 'upcoming'
              return (
                <div key={label} className={`schedule-stepper-item ${state}`}>
                  <span className="schedule-stepper-dot" aria-hidden="true">
                    {state === 'done' ? <Check size={12} weight="bold" /> : stepNum}
                  </span>
                  <span className="schedule-stepper-label">{label}</span>
                  {i < stepLabels.length - 1 && <span className="schedule-stepper-line" aria-hidden="true" />}
                </div>
              )
            })}
          </div>
        </div>

        {error && <p className="error">{error}</p>}

        {step === 1 && (
          <div className="schedule-modal-body schedule-modal-review">
            <div className="schedule-review-summary">
              <div className="schedule-preview-heading">
                <CalendarBlank size={16} />
                <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Selected Schedule</p>
              </div>
              <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
                {new Date(`${initialDate}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
              </p>
              <span className="schedule-modal-badge">Existing schedule</span>
              <dl className="schedule-summary-list" style={{ marginTop: 'var(--space-3)' }}>
                <dt>Department</dt>
                <dd>{departmentName(departmentIdStr)}</dd>
                <dt>Working hours</dt>
                <dd>
                  {editingGroup.map((b) => (
                    <div key={b.key}>{formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}</div>
                  ))}
                </dd>
                {hasBreaks && (
                  <>
                    <dt>Break</dt>
                    <dd>
                      {editingGroup.flatMap((b) => b.breaks).map((br, i) => (
                        <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                      ))}
                    </dd>
                  </>
                )}
              </dl>
              <p className="schedule-scope-summary" style={{ marginTop: 'var(--space-2)' }}>
                <Info size={16} weight="fill" />
                <span>You are about to remove this schedule. Existing appointments will not be cancelled or moved.</span>
              </p>
            </div>

            <div className="schedule-review-preview">
              <span className="field-label">What do you want to remove?</span>
              <div className="schedule-scope-cards edit" role="radiogroup" aria-label="Removal scope">
                {(['this_date', 'this_and_future', 'entire'] as EditScope[]).map((s) => {
                  const OptIcon = s === 'this_date' ? CalendarBlank : s === 'this_and_future' ? CaretDoubleRight : CalendarBlank
                  const selected = scope === s
                  return (
                    <button
                      key={s}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      className={`schedule-scope-card${selected ? ' selected' : ''}`}
                      onClick={() => setScope(s)}
                    >
                      <OptIcon size={22} weight={selected ? 'fill' : 'regular'} />
                      <span className="schedule-scope-card-title">{scopeLabel(s)}</span>
                      <span className="schedule-scope-card-desc">{scopeDescription(s)}</span>
                    </button>
                  )
                })}
              </div>

              {impactText && (
                <p className="schedule-scope-summary" style={{ marginTop: 'var(--space-2)' }}>
                  <Info size={16} weight="fill" />
                  <span><strong>This will remove:</strong> {impactText}</span>
                </p>
              )}

              {scope && (
                affectedAppointments.length > 0 ? (
                  <p className="schedule-scope-summary warning" style={{ marginTop: 'var(--space-2)' }}>
                    <Warning size={16} weight="fill" />
                    <span>
                      There {affectedAppointments.length === 1 ? 'is' : 'are'} {affectedAppointments.length} appointment
                      {affectedAppointments.length === 1 ? '' : 's'} associated with this schedule. Removing the
                      schedule will not cancel or move these appointments -- they will remain unchanged. Resolve
                      them separately if required.
                    </span>
                  </p>
                ) : (
                  <p className="schedule-scope-summary" style={{ marginTop: 'var(--space-2)' }}>
                    <Info size={16} weight="fill" />
                    <span>No existing appointments are affected. The schedule will be removed from the selected dates.</span>
                  </p>
                )
              )}
            </div>

            <div className="schedule-modal-footer schedule-modal-footer-full">
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>Cancel</button>
              <button type="button" className="btn btn-sm" disabled={!scope} onClick={() => setStep(2)}>
                Next
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="schedule-modal-body">
            <h4 className="schedule-review-summary-title">Review Impact</h4>
            <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
              This is what your schedule will look like after removal.
            </p>

            <div className="schedule-compare-grid two-col">
              <div className="schedule-compare-column">
                <div className="schedule-preview-heading">
                  <Clock size={16} />
                  <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>Before</p>
                </div>
                <dl className="schedule-summary-list" style={{ marginTop: 'var(--space-2)' }}>
                  <dt>Working hours</dt>
                  <dd>
                    {editingGroup.map((b) => (
                      <div key={b.key}>{formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}</div>
                    ))}
                  </dd>
                  {hasBreaks && (
                    <>
                      <dt>Break</dt>
                      <dd>
                        {editingGroup.flatMap((b) => b.breaks).map((br, i) => (
                          <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                        ))}
                      </dd>
                    </>
                  )}
                  <dt>Department</dt>
                  <dd>{departmentName(departmentIdStr)}</dd>
                </dl>
              </div>

              <div className="schedule-compare-column warning-column">
                <div className="schedule-preview-heading">
                  <Prohibit size={16} />
                  <p className="muted schedule-sidebar-note" style={{ margin: 0 }}>After</p>
                </div>
                <p className="schedule-summary-card-value" style={{ marginTop: 'var(--space-2)', fontWeight: 700, color: 'var(--color-danger)' }}>
                  No schedule
                </p>
                <p className="muted schedule-sidebar-note">
                  This {affectedCount !== null && affectedCount === 1 ? 'date' : 'range'} will have no working hours.
                </p>
              </div>
            </div>

            {requiresAck && (
              <label className="inline-label checkbox-label" style={{ marginTop: 'var(--space-3)' }}>
                <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
                I understand that this will remove the selected {scope === 'entire' ? 'recurring schedule' : 'schedule'}.
              </label>
            )}

            <div className="schedule-modal-footer schedule-modal-footer-full">
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(1)} disabled={busy}>
                Back
              </button>
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-sm" disabled={busy || (requiresAck && !acknowledged)} onClick={() => setStep(3)}>
                Next
              </button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="schedule-modal-body">
            <h4 className="schedule-review-summary-title">Remove this schedule?</h4>
            <p className="schedule-scope-summary warning">
              <Warning size={16} weight="fill" />
              <span>
                This action will remove the selected working hours from <strong>{affectedRangeText}</strong>.
                {impactText && ` ${impactText}`} Existing appointments will not be changed. This action cannot be
                undone automatically.
              </span>
            </p>

            <div className="schedule-summary-cards">
              <div className="schedule-summary-card">
                <Clock size={18} />
                <div className="schedule-summary-card-body">
                  <span className="schedule-summary-card-label">Working hours</span>
                  <span className="schedule-summary-card-value">
                    {editingGroup.map((b) => (
                      <div key={b.key}>{formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}</div>
                    ))}
                  </span>
                </div>
              </div>
              {hasBreaks && (
                <div className="schedule-summary-card">
                  <Coffee size={18} />
                  <div className="schedule-summary-card-body">
                    <span className="schedule-summary-card-label">Break</span>
                    <span className="schedule-summary-card-value">
                      {editingGroup.flatMap((b) => b.breaks).map((br, i) => (
                        <div key={i}>{formatTimeOfDay(br.start)} – {formatTimeOfDay(br.end)}</div>
                      ))}
                    </span>
                  </div>
                </div>
              )}
              <div className="schedule-summary-card">
                <Buildings size={18} />
                <div className="schedule-summary-card-body">
                  <span className="schedule-summary-card-label">Department</span>
                  <span className="schedule-summary-card-value">{departmentName(departmentIdStr)}</span>
                </div>
              </div>
            </div>

            <div className="schedule-modal-footer schedule-modal-footer-full">
              <button type="button" className="btn-secondary btn btn-sm" onClick={() => setStep(2)} disabled={busy}>
                Back
              </button>
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn-danger btn btn-sm" onClick={handleConfirmRemove} disabled={busy}>
                {busy ? 'Removing…' : 'Remove Schedule'}
              </button>
            </div>
          </div>
        )}

        {step === 'success' && (
          <div className="schedule-modal-body schedule-modal-success">
            <div className="schedule-success-check" aria-hidden="true">{'✓'}</div>
            <h3 style={{ margin: '0 0 4px' }}>Schedule removed successfully!</h3>
            <p className="muted" style={{ margin: 0 }}>The schedule was removed from:</p>
            <div className="schedule-summary-card success">
              <Prohibit size={18} />
              <div className="schedule-summary-card-body">
                <span className="schedule-summary-card-value schedule-success-headline">{savedSummary}</span>
                {impactText && <span className="schedule-summary-card-label">{impactText}</span>}
              </div>
            </div>
            <div className="schedule-modal-footer">
              <button type="button" className="btn btn-sm" onClick={onClose}>View Calendar</button>
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>Done</button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
