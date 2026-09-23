import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Info, Warning, X } from '@phosphor-icons/react'
import { ApiError, createDoctorBlock, deleteDoctorBlock, listAdminAppointments, updateDoctorBlock } from '../api'
import type { AdminAppointment, DoctorBlockEntry } from '../types'
import { formatDate, formatTimeOfDay, hasStarted } from '../format'
import { TimeCombobox } from '../components/ui/time-combobox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../components/ui/alert-dialog'
import AdminDatePicker from './AdminDatePicker'
import {
  blockLocalDateRange,
  blockIsFullDayOnDate,
  blockedMinutes,
  dayAvailability,
  formatDurationHours,
  instantToLocalDate,
  instantToLocalHHMM,
  toMinutesSinceMidnight,
  type ScheduleBlock,
} from './doctorSchedule'

// Curated quick-picks over the SAME free-text `reason` column
// doctor_blocks already has (app/api/doctor_blocks.py's DoctorBlockCreate
// -- min_length 1, max_length 255, no enum) -- not a new backend
// category. "Other" reveals a plain text field for anything else; note
// (optional) is appended onto that same string with an em dash, since
// there's no separate note column to reuse and adding one would be new
// backend infrastructure this redesign isn't meant to introduce.
const REASON_PRESETS = ['Personal leave', 'Medical leave', 'Conference', 'Vacation']
const NOTE_SEPARATOR = ' — '

type Duration = 'full_day' | 'specific_hours'

function decomposeReason(reason: string): { preset: string; customText: string } {
  if (REASON_PRESETS.includes(reason)) return { preset: reason, customText: '' }
  return { preset: 'Other', customText: reason }
}

// The inverse of how a Note gets folded into the same `reason` string
// on save (see NOTE_SEPARATOR above) -- for read-only display, not a
// new field: doctor_blocks still has just the one `reason` column.
function splitReasonAndNote(reason: string): { reason: string; note: string | null } {
  const idx = reason.indexOf(NOTE_SEPARATOR)
  if (idx === -1) return { reason, note: null }
  return { reason: reason.slice(0, idx), note: reason.slice(idx + NOTE_SEPARATOR.length) }
}

// A block is historical -- fully finished, not merely already begun --
// once its own end instant has passed. Reuses hasStarted (format.ts)
// exactly as appointment-start checks already do elsewhere; applying
// it to `end_at` rather than `start_at` is what turns "has this begun"
// into "has this completely ended".
function isPastBlock(block: DoctorBlockEntry): boolean {
  return hasStarted(block.end_at)
}

// Add/Edit/Manage Time Off -- ONE modal covers all three cases a date
// click can mean, never a separate popup:
//   - no existing block on this date  -> straight into the add form
//   - exactly one existing block      -> straight into editing it
//   - two or more existing blocks     -> a "manage" list for that date,
//     with Edit/Remove per block and "+ Add another time off" -- Edit
//     and Add-another both drop into the SAME form below, which
//     returns to this list on save rather than closing the modal.
// `forceAdd`/`forceEditBlock` bypass that auto-detection for the two
// call sites that already know exactly what they want (the page-level
// "+ Add Time Off" button, and the Upcoming Time Off list's own Edit
// action) -- both always get a simple add-or-edit-and-close, never the
// manage list, even if other blocks exist that date.
//
// Persists directly through the EXISTING doctor_blocks API
// (createDoctorBlock/updateDoctorBlock/deleteDoctorBlock in api.ts,
// thin wrappers over app/api/doctor_blocks.py) -- no new endpoint, no
// new model, no second validation system. The Availability Impact
// section reuses dayAvailability (doctorSchedule.ts) -- the exact same
// day-shape computation the Time Off calendar itself uses -- rather
// than a second, modal-local calculation.
export default function TimeOffModal({
  doctorId,
  blocks,
  initialDate,
  blocksForDate,
  forceAdd = false,
  forceEditBlock,
  onClose,
  onSaved,
}: {
  doctorId: number
  blocks: ScheduleBlock[]
  initialDate: string
  blocksForDate: DoctorBlockEntry[]
  forceAdd?: boolean
  forceEditBlock?: DoctorBlockEntry
  onClose: () => void
  onSaved: () => void
}) {
  // Computed once at mount -- later prop changes (e.g. blocksForDate
  // growing after "+ Add another time off") must never retroactively
  // flip this, or a plain single edit could suddenly gain a "back to
  // manage" button it never had.
  const [canReturnToManage] = useState(!forceAdd && !forceEditBlock && blocksForDate.length >= 2)
  const [view, setView] = useState<'manage' | 'form'>(canReturnToManage ? 'manage' : 'form')
  const [formEditingBlock, setFormEditingBlock] = useState<DoctorBlockEntry | null>(
    forceEditBlock ?? (!forceAdd && blocksForDate.length === 1 ? blocksForDate[0] : null),
  )
  const [removeTarget, setRemoveTarget] = useState<DoctorBlockEntry | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [manageError, setManageError] = useState<string | null>(null)

  // If a removal from the manage list empties it out, there's nothing
  // left to manage -- close rather than show an empty list.
  useEffect(() => {
    if (view === 'manage' && blocksForDate.length === 0) onClose()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, blocksForDate.length])

  async function confirmRemove() {
    if (!removeTarget) return
    setRemoveBusy(true)
    try {
      await deleteDoctorBlock(doctorId, removeTarget.id)
      // Removing the record currently open in the form/read-only view
      // (as opposed to a different card removed from the manage list)
      // leaves nothing left to show there -- back out of it rather than
      // leave a dangling reference to a now-deleted block.
      const wasViewedBlock = formEditingBlock?.id === removeTarget.id
      setRemoveTarget(null)
      onSaved()
      if (wasViewedBlock) {
        if (canReturnToManage) {
          setFormEditingBlock(null)
          setView('manage')
        } else {
          onClose()
        }
      }
    } catch (err) {
      setManageError(err instanceof ApiError ? err.message : 'Could not remove this time off')
    } finally {
      setRemoveBusy(false)
    }
  }

  const dateHeading = new Date(`${initialDate}T00:00:00`).toLocaleDateString('en-US', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })

  // A block that's already fully ended is historical -- shown read-only
  // (view/Remove only) rather than as an editable form, so an admin can
  // never silently move or extend a finished record into another past
  // period. Only ever relevant when `view === 'form'` and there's an
  // actual existing block loaded into it (never for a fresh Add).
  const viewingPastBlock = view === 'form' && formEditingBlock !== null && isPastBlock(formEditingBlock)

  return createPortal(
    <div className="modal-overlay schedule-modal-overlay" onClick={onClose}>
      <div
        className="modal-panel schedule-modal-panel schedule-modal-wide timeoff-modal-panel"
        role="dialog"
        aria-modal="true"
        aria-label={view === 'manage' ? 'Time Off' : viewingPastBlock ? 'Past Time Off' : formEditingBlock ? 'Edit Time Off' : 'Add Time Off'}
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>

        <div className="schedule-modal-header">
          <h3 className="appointment-details-heading" style={{ margin: 0 }}>
            {view === 'manage' ? 'Time Off' : viewingPastBlock ? 'Past Time Off' : formEditingBlock ? 'Edit Time Off' : 'Add Time Off'}
          </h3>
          <p className="muted schedule-modal-subtitle">
            {view === 'manage' ? dateHeading : viewingPastBlock ? dateHeading : 'Mark when the doctor will be unavailable.'}
          </p>
        </div>

        {manageError && <p className="error">{manageError}</p>}

        {view === 'manage' ? (
          <div className="schedule-modal-body timeoff-manage-body">
            <span className="field-label">Existing time off</span>
            <div className="timeoff-manage-list">
              {blocksForDate.map((b) => {
                const fullDay = blockIsFullDayOnDate(b, initialDate)
                return (
                  <div key={b.id} className="timeoff-manage-card">
                    <div className="timeoff-manage-card-body">
                      <div className="timeoff-manage-card-time">
                        <span className={`schedule-legend-dot ${fullDay ? 'time_off' : 'partial'}`} aria-hidden="true" />
                        {fullDay
                          ? 'Full day'
                          : `${formatTimeOfDay(instantToLocalHHMM(b.start_at))} – ${formatTimeOfDay(instantToLocalHHMM(b.end_at))}`}
                      </div>
                      <span className="muted">{b.reason}</span>
                    </div>
                    <div className="timeoff-manage-card-actions">
                      <button
                        type="button" className="btn-secondary btn btn-sm"
                        onClick={() => { setFormEditingBlock(b); setView('form') }}
                      >
                        Edit
                      </button>
                      <button
                        type="button" className="btn-danger-outline btn btn-sm"
                        onClick={() => setRemoveTarget(b)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
            <button
              type="button" className="link"
              onClick={() => { setFormEditingBlock(null); setView('form') }}
            >
              + Add another time off
            </button>

            <div className="schedule-modal-footer">
              <button type="button" className="btn-secondary btn btn-sm" onClick={onClose}>
                Close
              </button>
            </div>
          </div>
        ) : viewingPastBlock && formEditingBlock ? (
          <TimeOffPastRecordView
            block={formEditingBlock}
            initialDate={initialDate}
            onRemove={() => setRemoveTarget(formEditingBlock)}
            onClose={() => (canReturnToManage ? setView('manage') : onClose())}
          />
        ) : (
          <TimeOffForm
            key={formEditingBlock?.id ?? 'new'}
            doctorId={doctorId}
            blocks={blocks}
            initialDate={initialDate}
            editingBlock={formEditingBlock}
            onCancel={() => (canReturnToManage ? setView('manage') : onClose())}
            onSaved={() => {
              onSaved()
              if (canReturnToManage) {
                setFormEditingBlock(null)
                setView('manage')
              } else {
                onClose()
              }
            }}
          />
        )}
      </div>

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove Time Off?</AlertDialogTitle>
            <AlertDialogDescription>
              The doctor will become available during this period again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove} disabled={removeBusy}>
              {removeBusy ? 'Removing…' : 'Remove Time Off'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>,
    document.body,
  )
}

// Read-only view for a block whose end_at has already passed -- a
// completed, historical record. Never editable (there's nothing
// meaningful to change on a period that's already over, and silently
// letting an admin drag a finished block into a different past window
// would just create confusing data), but Remove stays available since
// a wrongly-entered historical record still needs a way out.
function TimeOffPastRecordView({
  block,
  initialDate,
  onRemove,
  onClose,
}: {
  block: DoctorBlockEntry
  initialDate: string
  onRemove: () => void
  onClose: () => void
}) {
  const fullDay = blockIsFullDayOnDate(block, initialDate)
  const { reason, note } = splitReasonAndNote(block.reason)
  const dateHeading = new Date(`${initialDate}T00:00:00`).toLocaleDateString('en-US', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })

  return (
    <>
      <div className="schedule-modal-body timeoff-modal-body">
        <p className="schedule-scope-summary warning">
          <Warning size={16} weight="fill" />
          <span>This time-off period has already ended and can no longer be edited.</span>
        </p>

        <div className="timeoff-impact-card">
          <div className="timeoff-impact-date">{dateHeading}</div>
          <dl className="schedule-summary-list">
            <dt>Time</dt>
            <dd>
              {fullDay
                ? 'Full day'
                : `${formatTimeOfDay(instantToLocalHHMM(block.start_at))} – ${formatTimeOfDay(instantToLocalHHMM(block.end_at))}`}
            </dd>
            <dt>Reason</dt>
            <dd>{reason}</dd>
            {note && (
              <>
                <dt>Note</dt>
                <dd>{note}</dd>
              </>
            )}
          </dl>
        </div>
      </div>

      <div className="schedule-modal-footer schedule-modal-footer-split">
        <div>
          <button type="button" className="btn-danger-outline btn btn-sm" onClick={onRemove}>
            Remove Time Off
          </button>
        </div>
        <div className="schedule-modal-footer-actions">
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </>
  )
}

// The actual add/edit form -- three numbered sections (When / Reason /
// Availability impact) in one scroll, no wizard pagination (the
// reference shows all three visible at once, and nothing here benefits
// from being paginated). Remounted (via the parent's `key`) whenever
// which block it's editing changes, so every piece of local state
// below is naturally correct for that block on mount -- no manual
// reset effects needed.
function TimeOffForm({
  doctorId,
  blocks,
  initialDate,
  editingBlock,
  onCancel,
  onSaved,
}: {
  doctorId: number
  blocks: ScheduleBlock[]
  initialDate: string
  editingBlock: DoctorBlockEntry | null
  onCancel: () => void
  onSaved: () => void
}) {
  const editRange = editingBlock ? blockLocalDateRange(editingBlock) : null
  const editIsFullDay = editingBlock && editRange ? blockIsFullDayOnDate(editingBlock, editRange.startDate) : false
  const editReason = editingBlock ? decomposeReason(editingBlock.reason) : { preset: REASON_PRESETS[0], customText: '' }

  const [startDate, setStartDate] = useState(editRange?.startDate ?? initialDate)
  const [endDate, setEndDate] = useState(editRange?.endDate ?? initialDate)
  const [duration, setDuration] = useState<Duration>(editingBlock ? (editIsFullDay ? 'full_day' : 'specific_hours') : 'full_day')
  const [fromTime, setFromTime] = useState(editingBlock && !editIsFullDay ? instantToLocalHHMM(editingBlock.start_at) : '09:00')
  const [untilTime, setUntilTime] = useState(editingBlock && !editIsFullDay ? instantToLocalHHMM(editingBlock.end_at) : '10:00')
  const [reasonPreset, setReasonPreset] = useState(editReason.preset)
  const [customReason, setCustomReason] = useState(editReason.customText)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [affectedAppointments, setAffectedAppointments] = useState<AdminAppointment[]>([])

  // Specific hours is one continuous instant range in the underlying
  // model (start_at..end_at, a single row) -- "9-10am every day for a
  // week" isn't representable as one row, and silently creating several
  // rows for it would turn one Time Off record into several with their
  // own independent edit/remove lifecycle. So Specific hours is
  // single-date only; a multi-day range requires Full day.
  function selectDuration(next: Duration) {
    setDuration(next)
    if (next === 'specific_hours' && endDate !== startDate) setEndDate(startDate)
  }
  function changeStartDate(v: string) {
    setStartDate(v)
    if (duration === 'specific_hours') setEndDate(v)
    else if (endDate < v) setEndDate(v)
  }

  const dateRangeValid = !!startDate && !!endDate && startDate <= endDate

  // A completely past date is never a valid NEW start date -- "today"
  // in the same fixed +05:30 terms every instant in this file already
  // assumes (instantToLocalDate, not useClinicToday, so this can never
  // drift from the offset actually baked into start_at/end_at below).
  // Full day on TODAY is still allowed: its 00:00 start is bookkeeping,
  // not a real chosen time, and it still blocks the rest of today -- a
  // genuine future effect, not "entirely past".
  const todayIso = instantToLocalDate(new Date().toISOString())
  const startDateIsPast = !!startDate && startDate < todayIso
  // Specific hours' own chosen start only matters once it's actually
  // today -- hasStarted compares the real instant, so this is exactly
  // the same "has this begun" check appointment-start logic already
  // uses, just aimed at the drafted block's own start_at.
  const startTimeIsPast =
    duration === 'specific_hours' && startDate === todayIso && hasStarted(`${startDate}T${fromTime}:00+05:30`)

  const hoursValid = duration === 'full_day' || fromTime < untilTime
  const reasonText = reasonPreset === 'Other' ? customReason.trim() : reasonPreset
  const reasonValid = reasonText.length > 0
  const formValid = dateRangeValid && !startDateIsPast && !startTimeIsPast && hoursValid && reasonValid
  // doctor_blocks.reason is capped at 255 chars (DoctorBlockCreate);
  // note shares that same column when composed on save, so its own
  // room is whatever's left after the reason text and the separator.
  const noteMaxLength = Math.max(0, 255 - reasonText.length - NOTE_SEPARATOR.length)

  const savedReason = note.trim() ? `${reasonText}${NOTE_SEPARATOR}${note.trim()}` : reasonText

  function draftBlockEntry(): DoctorBlockEntry {
    const startAt = duration === 'full_day' ? `${startDate}T00:00:00+05:30` : `${startDate}T${fromTime}:00+05:30`
    const endAt = duration === 'full_day' ? `${endDate}T23:59:00+05:30` : `${startDate}T${untilTime}:00+05:30`
    return { id: editingBlock?.id ?? -1, start_at: startAt, end_at: endAt, reason: savedReason, active: true, created_at: '' }
  }

  // Availability Impact -- single date shown in full (the common case);
  // a multi-day Full day range gets a condensed summary instead of one
  // day repeated N times. Never computed for a past/invalid draft --
  // a timeline for a range that can't actually be saved would just be
  // misleading.
  const rangeValidForPreview = dateRangeValid && !startDateIsPast && !startTimeIsPast
  const draft = rangeValidForPreview ? draftBlockEntry() : null
  const isMultiDay = startDate !== endDate
  const impactDate = startDate
  const impact = draft && !isMultiDay ? dayAvailability(impactDate, blocks, [draft]) : null
  const impactBlockedMinutes = impact ? blockedMinutes(impact.segments) : 0

  // Existing appointments during the drafted period -- a separate
  // concern from availability shape: reused via the same
  // listAdminAppointments the Appointments tab and Configure Schedule's
  // own appointment warnings already call, filtered to statuses that
  // still occupy a slot. Never auto-cancelled/moved from here.
  useEffect(() => {
    if (!dateRangeValid) {
      setAffectedAppointments([])
      return
    }
    listAdminAppointments({ doctor_id: doctorId, date_from: startDate, date_to: endDate })
      .then(({ items: all }) => {
        const relevant = all.filter((a) => ['PENDING', 'CONFIRMED', 'CHECKED_IN'].includes(a.status))
        const inWindow = relevant.filter((a) => {
          const apptDate = instantToLocalDate(a.start_at)
          if (duration === 'full_day') return apptDate >= startDate && apptDate <= endDate
          if (apptDate !== startDate) return false
          const apptStart = toMinutesSinceMidnight(instantToLocalHHMM(a.start_at))
          const apptEnd = toMinutesSinceMidnight(instantToLocalHHMM(a.end_at))
          const offStart = toMinutesSinceMidnight(fromTime)
          const offEnd = toMinutesSinceMidnight(untilTime)
          return apptStart < offEnd && apptEnd > offStart
        })
        setAffectedAppointments(inWindow)
      })
      .catch(() => setAffectedAppointments([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doctorId, startDate, endDate, duration, fromTime, untilTime])

  async function handleSave() {
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      if (editingBlock) {
        await updateDoctorBlock(doctorId, editingBlock.id, draft.start_at, draft.end_at, draft.reason)
      } else {
        await createDoctorBlock(doctorId, draft.start_at, draft.end_at, draft.reason)
      }
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save this time off')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {error && <p className="error">{error}</p>}

      <div className="schedule-modal-body timeoff-modal-body">
        <section className="timeoff-modal-section">
          <div className="timeoff-section-heading">
            <span className="schedule-stepper-dot timeoff-section-number" aria-hidden="true">1</span>
            <span className="field-label">When is the doctor unavailable?</span>
          </div>

          <div className="schedule-field-grid">
            <div className="schedule-field-group">
              <span className="field-label">Start date</span>
              <AdminDatePicker value={startDate} onChange={changeStartDate} placeholder="Select date" />
            </div>
            <div className="schedule-field-group">
              <span className="field-label">End date</span>
              <AdminDatePicker
                value={endDate}
                onChange={setEndDate}
                placeholder="Select date"
              />
            </div>
          </div>
          {duration === 'specific_hours' && (
            <p className="muted schedule-sidebar-note" style={{ margin: '0 0 8px' }}>
              Specific hours applies to a single date. Choose Full day for a multi-day range.
            </p>
          )}
          {!dateRangeValid && startDate && endDate && (
            <p className="schedule-scope-validation">End date must be on or after the start date.</p>
          )}
          {dateRangeValid && startDateIsPast && (
            <p className="schedule-scope-validation">
              Time off can only be added for today's remaining time or a future date.
            </p>
          )}

          <div className="schedule-field-group">
            <span className="field-label">Duration</span>
            <div className="timeoff-duration-cards" role="radiogroup" aria-label="Duration">
              <button
                type="button" role="radio" aria-checked={duration === 'full_day'}
                className={`timeoff-duration-card${duration === 'full_day' ? ' selected' : ''}`}
                onClick={() => selectDuration('full_day')}
              >
                <span className="timeoff-duration-card-title">Full day</span>
                <span className="timeoff-duration-card-desc">Unavailable for the entire day.</span>
              </button>
              <button
                type="button" role="radio" aria-checked={duration === 'specific_hours'}
                className={`timeoff-duration-card${duration === 'specific_hours' ? ' selected' : ''}`}
                onClick={() => selectDuration('specific_hours')}
              >
                <span className="timeoff-duration-card-title">Specific hours</span>
                <span className="timeoff-duration-card-desc">Unavailable for a particular time range.</span>
              </button>
            </div>
          </div>

          {duration === 'specific_hours' && (
            <div className="schedule-field-grid">
              <div className="schedule-field-group">
                <span className="field-label">From</span>
                <TimeCombobox
                  value={fromTime}
                  onChange={setFromTime}
                  durationMinutes={30}
                  ariaLabel="Time off start time"
                  disabledOptions={
                    startDate === todayIso ? (v) => hasStarted(`${startDate}T${v}:00+05:30`) : undefined
                  }
                />
              </div>
              <div className="schedule-field-group">
                <span className="field-label">Until</span>
                <TimeCombobox
                  value={untilTime}
                  onChange={setUntilTime}
                  durationMinutes={30}
                  ariaLabel="Time off end time"
                  disabledOptions={(v) => v <= fromTime}
                />
              </div>
            </div>
          )}
          {duration === 'specific_hours' && !hoursValid && (
            <p className="schedule-scope-validation">"Until" must be after "From".</p>
          )}
          {duration === 'specific_hours' && hoursValid && startTimeIsPast && (
            <p className="schedule-scope-validation">This time has already passed. Select a future time.</p>
          )}
        </section>

        <section className="timeoff-modal-section">
          <div className="timeoff-section-heading">
            <span className="schedule-stepper-dot timeoff-section-number" aria-hidden="true">2</span>
            <span className="field-label">Why is the doctor unavailable?</span>
          </div>

          <div className="schedule-field-group">
            <span className="field-label">Reason</span>
            <Select value={reasonPreset} onValueChange={setReasonPreset}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REASON_PRESETS.map((r) => (
                  <SelectItem key={r} value={r}>{r}</SelectItem>
                ))}
                <SelectItem value="Other">Other</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {reasonPreset === 'Other' && (
            <div className="schedule-field-group">
              <input
                value={customReason}
                onChange={(e) => setCustomReason(e.target.value)}
                placeholder="Describe the reason"
                maxLength={255}
              />
            </div>
          )}

          <label className="schedule-field-group">
            <span className="field-label">Note (optional)</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Add a short note..."
              rows={2}
              maxLength={noteMaxLength}
            />
            <span className="muted schedule-sidebar-note" style={{ textAlign: 'right' }}>
              {note.length}/{noteMaxLength}
            </span>
          </label>
        </section>

        <section className="timeoff-modal-section">
          <div className="timeoff-section-heading">
            <span className="schedule-stepper-dot timeoff-section-number" aria-hidden="true">3</span>
            <span className="field-label">Availability impact</span>
          </div>

          {!dateRangeValid ? (
            <p className="muted schedule-sidebar-note">Choose valid dates to see the availability impact.</p>
          ) : !rangeValidForPreview ? (
            <p className="muted schedule-sidebar-note">Choose a valid, future date and time to see the availability impact.</p>
          ) : isMultiDay ? (
            <p className="schedule-scope-summary">
              <Info size={16} weight="fill" />
              <span>
                This time off spans {formatDate(startDate)} – {formatDate(endDate)}. The doctor will be unavailable
                for the entire duration each day; the recurring schedule itself is not changed or deleted.
              </span>
            </p>
          ) : impact ? (
            <div className="timeoff-impact-card">
              <div className="timeoff-impact-date">
                {new Date(`${impactDate}T00:00:00`).toLocaleDateString('en-US', {
                  weekday: 'long', day: 'numeric', month: 'long',
                })}
              </div>
              <dl className="schedule-summary-list" style={{ marginBottom: 'var(--space-2)' }}>
                <dt>Regular schedule</dt>
                <dd>
                  {impact.workingBlocks.length > 0
                    ? impact.workingBlocks.map((b, i) => (
                        <div key={i}>{formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}</div>
                      ))
                    : 'No working schedule on this date'}
                </dd>
                <dt>Time off</dt>
                <dd>{duration === 'full_day' ? 'Full day' : `${formatTimeOfDay(fromTime)} – ${formatTimeOfDay(untilTime)}`}</dd>
              </dl>

              {impact.segments.length > 0 && (
                <div className="slots-timeline">
                  {impact.segments.map((seg, i) => (
                    <div key={i} className="slots-timeline-row">
                      <span className="slots-timeline-time">{formatTimeOfDay(seg.start)}</span>
                      <div className={`slots-timeline-content ${seg.type === 'time_off' ? 'break' : 'slot'}`}>
                        <div className={`slots-timeline-chip ${seg.type === 'time_off' ? 'break' : 'slot'}`}>
                          <span>{formatTimeOfDay(seg.start)} – {formatTimeOfDay(seg.end)}</span>
                          <span className="slots-timeline-badge">
                            {seg.type === 'time_off' ? 'Time off' : 'Available'}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <p className="schedule-scope-summary" style={{ marginTop: 'var(--space-2)' }}>
                <Info size={16} weight="fill" />
                <span>
                  {impact.workingBlocks.length === 0
                    ? 'There is no working schedule on this date, so this time off will not change bookable availability.'
                    : impactBlockedMinutes > 0
                      ? `This will block ${formatDurationHours(impactBlockedMinutes)} of availability.`
                      : 'This time off falls outside the working hours on this date, so it will not change bookable availability.'}
                </span>
              </p>
            </div>
          ) : null}

          {affectedAppointments.length > 0 && (
            <p className="schedule-scope-summary warning">
              <Warning size={16} weight="fill" />
              <span>
                <strong>Existing appointments</strong>
                <br />
                {affectedAppointments.length} appointment{affectedAppointments.length === 1 ? ' is' : 's are'} already
                booked during this period.
                <br />
                Adding time off will not automatically cancel or move {affectedAppointments.length === 1 ? 'this appointment' : 'these appointments'}.
              </span>
            </p>
          )}
        </section>
      </div>

      <div className="schedule-modal-footer schedule-modal-footer-full">
        <button type="button" className="btn-secondary btn btn-sm" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="btn btn-sm" onClick={handleSave} disabled={busy || !formValid}>
          {busy ? 'Saving…' : editingBlock ? 'Save Changes' : 'Add Time Off'}
        </button>
      </div>
    </>
  )
}
