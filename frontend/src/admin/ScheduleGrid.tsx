import { useEffect, useMemo, useState } from 'react'
import { ApiError, createDoctorSchedule, deleteDoctorSchedule, getDoctorBlocks, getDoctorDepartments, getDoctorScheduleAdmin, updateDoctorSlotSettings } from '../api'
import type { Department, Doctor, DoctorBlockEntry, DoctorScheduleEntry } from '../types'
import { formatDate } from '../format'
import ScheduleMonthView from './ScheduleMonthView'
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
import {
  DAY_NAMES,
  DEFAULT_MERGE_GAP_MINUTES,
  DURATION_OPTIONS,
  dateToDayOfWeek,
  isoDateToday,
  mergeEntriesIntoBlocks,
  rangeKey,
  rowTupleKey,
  blocksToRowPayloads,
  copyBlockToDay,
  splitBlockForEdit,
  templateDaySlots,
  validateBlockBreaks,
  type EditScope,
  type ScheduleBlock,
} from './doctorSchedule'

const DAYS = [1, 2, 3, 4, 5, 6, 7]

// The Schedule tab -- one workflow, the monthly calendar
// (ScheduleMonthView.tsx: select a date, Configure Schedule modal,
// live preview, save), plus Copy schedule and the Slot settings/
// week-at-a-glance preview sidebar. There used to also be a 7x24
// drag-to-paint weekly grid as a secondary "advanced" view; it was
// removed (not just hidden) so there is exactly one way to manage a
// doctor's schedule -- its bulk-editing use case (many days/hours at
// once) is covered by the Configure Schedule modal's own "Selected
// days"/"Custom date range" scope plus Copy schedule.
export default function ScheduleGrid({
  doctor,
  isAdmin,
  onDirtyChange,
}: {
  doctor: Doctor
  isAdmin: boolean
  onDirtyChange?: (dirty: boolean) => void
}) {
  const [entries, setEntries] = useState<DoctorScheduleEntry[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  // One-off blocks (Time off tab) -- read-only context for the monthly
  // summary view's "Time off" status, never edited from here. Kept
  // separate from doctor_schedule/draftBlocks on purpose: mixing the two
  // into one editable model is exactly the "preview vs real availability"
  // conflation the PLAN said to keep apart.
  const [oneOffBlocks, setOneOffBlocks] = useState<DoctorBlockEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [savedFlash, setSavedFlash] = useState(false)

  const savedBlocks = useMemo(
    () => mergeEntriesIntoBlocks(entries, DEFAULT_MERGE_GAP_MINUTES),
    [entries],
  )
  const [draftBlocks, setDraftBlocks] = useState<ScheduleBlock[]>([])

  const [defaultDuration, setDefaultDuration] = useState(doctor.default_duration_minutes)
  const [bufferMinutes, setBufferMinutes] = useState(doctor.buffer_minutes)
  const [savedDuration, setSavedDuration] = useState(doctor.default_duration_minutes)
  const [savedBuffer, setSavedBuffer] = useState(doctor.buffer_minutes)
  const slotSettingsDirty = defaultDuration !== savedDuration || bufferMinutes !== savedBuffer

  const [confirmReconcileOpen, setConfirmReconcileOpen] = useState(false)

  // Editing an existing, persisted block that spans more than the one
  // date it's being edited from needs an explicit "how far should this
  // reach" answer before the edit is applied at all -- see
  // splitBlockForEdit's own docstring. `pendingEdit` holds the in-flight
  // edit (or removal, mutator === null) while that choice dialog is
  // open; a brand-new or already single-day block never reaches this
  // (requestBlockEdit/requestBlockRemove apply those immediately).
  const [pendingEdit, setPendingEdit] = useState<{
    block: ScheduleBlock
    dateStr: string
    mutator: ((b: ScheduleBlock) => ScheduleBlock) | null
  } | null>(null)

  // "Copy schedule" -- copies one day's blocks (times, breaks,
  // department) onto other days, staged into the draft like any other
  // edit (see copyBlockToDay's own docstring for why conflicting target
  // days are skipped rather than aborting the whole copy).
  const [showCopyForm, setShowCopyForm] = useState(false)
  const [copySourceDay, setCopySourceDay] = useState('')
  const [copyTargetDays, setCopyTargetDays] = useState<number[]>([])
  const [copyError, setCopyError] = useState<string | null>(null)

  function load() {
    Promise.all([getDoctorScheduleAdmin(doctor.id), getDoctorDepartments(doctor.id), getDoctorBlocks(doctor.id)])
      .then(([schedule, depts, blocks]) => {
        setEntries(schedule)
        setDepartments(depts)
        setOneOffBlocks(blocks)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load schedule'))
  }
  useEffect(load, [doctor.id])

  // Reset the draft to a fresh copy of what's actually persisted
  // whenever `entries` changes (initial load, or after a successful
  // save's own reload) -- mirrors the old form's load()/resetForm() pair.
  useEffect(() => {
    setDraftBlocks(mergeEntriesIntoBlocks(entries, DEFAULT_MERGE_GAP_MINUTES))
  }, [entries])

  // A day's applicable blocks -- the first date-range group found for
  // that weekday (a day almost always has just one; Copy schedule and
  // the week-at-a-glance preview don't need to pick among several).
  function blocksForDay(day: number): ScheduleBlock[] {
    const ranges = [...new Set(draftBlocks.filter((b) => b.day === day).map((b) => rangeKey(b.startDate, b.endDate)))]
    const active = ranges[0] ?? rangeKey(null, null)
    return draftBlocks
      .filter((b) => b.day === day && rangeKey(b.startDate, b.endDate) === active)
      .sort((a, b) => a.startTime.localeCompare(b.startTime))
  }

  // -- Desired-vs-persisted diff (also this section's dirty flag) --------
  // blockSegments() is defined so an *unedited* block reproduces exactly
  // the rows it was merged from, so a plain tuple-set diff against the
  // real `entries` is enough to know what changed -- no id-matching or
  // PUT needed, just POST the new tuples and DELETE the dropped ones.
  const desiredPayloads = useMemo(() => blocksToRowPayloads(draftBlocks), [draftBlocks])
  const desiredKeys = useMemo(() => new Set(desiredPayloads.map(rowTupleKey)), [desiredPayloads])
  const existingActive = useMemo(() => entries.filter((e) => e.active), [entries])
  const existingKeys = useMemo(() => new Set(existingActive.map(rowTupleKey)), [existingActive])
  const scheduleDirty =
    desiredKeys.size !== existingKeys.size || [...desiredKeys].some((k) => !existingKeys.has(k))
  const overallDirty = scheduleDirty || slotSettingsDirty

  useEffect(() => {
    onDirtyChange?.(overallDirty)
    return () => onDirtyChange?.(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overallDirty])

  function updateBlock(key: string, mutator: (b: ScheduleBlock) => ScheduleBlock) {
    setDraftBlocks((prev) => prev.map((b) => (b.key === key ? mutator(b) : b)))
  }

  function removeBlock(key: string) {
    setDraftBlocks((prev) => prev.filter((b) => b.key !== key))
  }

  // Does `block` (as currently in draftBlocks, before any edit) already
  // span more than the single date it's being edited from? Only then is
  // "how far should this change reach" actually ambiguous -- a block
  // that's brand new (no sourceIds -- nothing persisted yet to
  // disambiguate against) or already scoped to just this one date needs
  // no prompt at all.
  function editIsAmbiguous(block: ScheduleBlock, dateStr: string): boolean {
    return block.sourceIds.length > 0 && !(block.startDate === dateStr && block.endDate === dateStr)
  }

  function requestBlockEdit(block: ScheduleBlock, dateStr: string, mutator: (b: ScheduleBlock) => ScheduleBlock) {
    if (!editIsAmbiguous(block, dateStr)) {
      updateBlock(block.key, mutator)
      return
    }
    setPendingEdit({ block, dateStr, mutator })
  }

  function requestBlockRemove(block: ScheduleBlock, dateStr: string) {
    if (!editIsAmbiguous(block, dateStr)) {
      removeBlock(block.key)
      return
    }
    setPendingEdit({ block, dateStr, mutator: null })
  }

  function resolvePendingEdit(scope: EditScope) {
    if (!pendingEdit) return
    const { block, dateStr, mutator } = pendingEdit
    const replacements = splitBlockForEdit(block, dateStr, scope, mutator)
    setDraftBlocks((prev) => [...prev.filter((b) => b.key !== block.key), ...replacements])
    setPendingEdit(null)
  }

  function handleCopyFromDate(dateStr: string) {
    setCopySourceDay(String(dateToDayOfWeek(dateStr)))
    setCopyTargetDays([])
    setCopyError(null)
    setShowCopyForm(true)
  }

  function toggleCopyTargetDay(day: number) {
    setCopyTargetDays((prev) => (prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort((a, b) => a - b)))
  }

  function handleCopySchedule() {
    setCopyError(null)
    const sourceDay = Number(copySourceDay)
    if (!copySourceDay) {
      setCopyError('Pick a day to copy hours from.')
      return
    }
    const sourceBlocks = blocksForDay(sourceDay)
    if (sourceBlocks.length === 0 || copyTargetDays.length === 0) {
      setCopyError('Pick a source day with hours and at least one target day.')
      return
    }
    let next = draftBlocks
    let copiedCount = 0
    for (const targetDay of copyTargetDays) {
      const targetBlocks = blocksForDay(targetDay)
      const startDate = targetBlocks[0]?.startDate ?? null
      const endDate = targetBlocks[0]?.endDate ?? null
      for (const b of sourceBlocks) {
        const result = copyBlockToDay(next, targetDay, b, startDate, endDate)
        next = result.blocks
        if (result.copied) copiedCount++
      }
    }
    setDraftBlocks(next)
    setShowCopyForm(false)
    setCopySourceDay('')
    setCopyTargetDays([])
    setCopyError(copiedCount === 0 ? 'Nothing was copied -- the target days already have overlapping hours.' : null)
  }

  // -- Save / Cancel --------------------------------------------------------
  function anyBlockingError(): string | null {
    for (const b of draftBlocks) {
      if (!(b.startTime < b.endTime)) return `${DAY_NAMES[b.day]} shift has an invalid time range`
      const berr = validateBlockBreaks(b.startTime, b.endTime, b.breaks)
      if (berr) return `${DAY_NAMES[b.day]}: ${berr}`
    }
    return null
  }

  // A dirty, previously-saved block whose range still reaches forward
  // (open-ended, or a repeat-until date not yet passed) -- editing it
  // changes the single persisted row range that *is* the repeating
  // pattern, so there's no separate set of future weeks to reconcile;
  // this is what the confirm dialog explains before Save commits it.
  const today = isoDateToday()
  const hasForwardRepeatingEdit = draftBlocks.some((b) => {
    if (b.sourceIds.length === 0) return false
    if (b.endDate !== null && b.endDate < today) return false
    const segs = blocksToRowPayloads([b]).map(rowTupleKey)
    return segs.some((k) => !existingKeys.has(k))
  })

  async function doSave() {
    setBusy(true)
    setError(null)
    try {
      if (slotSettingsDirty) {
        await updateDoctorSlotSettings(doctor.id, {
          default_duration_minutes: defaultDuration,
          buffer_minutes: bufferMinutes,
        })
        setSavedDuration(defaultDuration)
        setSavedBuffer(bufferMinutes)
      }
      if (scheduleDirty) {
        const toDelete = existingActive.filter((e) => !desiredKeys.has(rowTupleKey(e)))
        const toCreate = desiredPayloads.filter((d) => !existingKeys.has(rowTupleKey(d)))
        for (const row of toDelete) {
          await deleteDoctorSchedule(doctor.id, row.id)
        }
        for (const row of toCreate) {
          await createDoctorSchedule(doctor.id, row)
        }
      }
      load()
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 2500)
    } catch (err) {
      setError(
        (err instanceof ApiError ? err.message : 'Could not save schedule') +
          ' -- reloading the last saved schedule; please redo any remaining changes.',
      )
      load()
    } finally {
      setBusy(false)
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const blockingError = anyBlockingError()
    if (blockingError) {
      setError(blockingError)
      return
    }
    if (scheduleDirty && hasForwardRepeatingEdit) {
      setConfirmReconcileOpen(true)
      return
    }
    await doSave()
  }

  function handleCancel() {
    setDraftBlocks(savedBlocks)
    setDefaultDuration(savedDuration)
    setBufferMinutes(savedBuffer)
    setError(null)
  }

  const saveDisabled = busy || !overallDirty
  const cancelDisabled = busy || !overallDirty

  return (
    <div>
      <div className={overallDirty ? 'schedule-tab-layout is-dirty' : 'schedule-tab-layout'}>
      <div className="schedule-tab-main">
      <div className="admin-content-header">
        <div>
          <h4 style={{ margin: 0 }}>Working hours</h4>
          <p className="muted" style={{ margin: 0 }}>
            Click a date to see and edit that day’s working hours.
          </p>
        </div>
      </div>
      <div className="schedule-header-actions">
        {isAdmin && (
          <button type="button" className="btn-secondary btn btn-sm" onClick={() => setShowCopyForm((v) => !v)}>
            {showCopyForm ? 'Cancel copy' : 'Copy schedule'}
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}

      {isAdmin && showCopyForm && (
        <form
          className="inline-form wrap"
          onSubmit={(e) => {
            e.preventDefault()
            handleCopySchedule()
          }}
        >
          <label className="inline-label">
            Copy hours from
            <select value={copySourceDay} onChange={(e) => setCopySourceDay(e.target.value)} required>
              <option value="">Choose a day…</option>
              {DAYS.filter((d) => blocksForDay(d).length > 0).map((d) => (
                <option key={d} value={d}>
                  {DAY_NAMES[d]}
                </option>
              ))}
            </select>
          </label>
          <div style={{ width: '100%' }}>
            <span className="field-label">To</span>
            <div className="day-multiselect" role="group" aria-label="Target days">
              {DAY_NAMES.slice(1).map((name, i) => {
                const day = i + 1
                const isSource = copySourceDay !== '' && Number(copySourceDay) === day
                return (
                  <button
                    key={day}
                    type="button"
                    className={copyTargetDays.includes(day) ? 'selected' : ''}
                    aria-pressed={copyTargetDays.includes(day)}
                    disabled={isSource}
                    onClick={() => toggleCopyTargetDay(day)}
                  >
                    {name.slice(0, 3)}
                  </button>
                )
              })}
            </div>
          </div>
          {copyError && <p className="error">{copyError}</p>}
          <button type="submit">Copy hours</button>
        </form>
      )}

      <ScheduleMonthView
        doctorId={doctor.id}
        draftBlocks={draftBlocks}
        oneOffBlocks={oneOffBlocks}
        departments={departments}
        defaultDuration={defaultDuration}
        bufferMinutes={bufferMinutes}
        overallDirty={overallDirty}
        isAdmin={isAdmin}
        onScheduleSaved={load}
        onEditBlock={requestBlockEdit}
        onRemoveBlock={requestBlockRemove}
        onCopyFromDate={handleCopyFromDate}
      />

      <AlertDialog open={pendingEdit !== null} onOpenChange={(open) => !open && setPendingEdit(null)}>
        <AlertDialogContent className="max-w-[460px]">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingEdit && pendingEdit.mutator === null ? 'Remove which occurrences?' : 'Apply this change to which dates?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingEdit && (
                <>
                  This schedule applies to every {DAY_NAMES[pendingEdit.block.day]}
                  {pendingEdit.block.startDate || pendingEdit.block.endDate ? (
                    <>
                      {' from '}
                      {pendingEdit.block.startDate ? formatDate(pendingEdit.block.startDate) : 'the start'}
                      {' to '}
                      {pendingEdit.block.endDate ? formatDate(pendingEdit.block.endDate) : 'no end date'}
                    </>
                  ) : (
                    ', with no end date'
                  )}
                  . Choose how far this {pendingEdit.mutator === null ? 'removal' : 'change'} should reach.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2">
            <AlertDialogAction className="w-full justify-start" onClick={() => resolvePendingEdit('this_date')}>
              Only {pendingEdit && formatDate(pendingEdit.dateStr)}
            </AlertDialogAction>
            <AlertDialogAction
              className="w-full justify-start bg-[var(--color-surface)] text-[var(--color-primary-hover)] border border-[var(--color-border-strong)] hover:bg-[var(--color-primary-soft)]"
              onClick={() => resolvePendingEdit('this_and_future')}
            >
              This and future occurrences
            </AlertDialogAction>
            <AlertDialogAction
              className="w-full justify-start bg-[var(--color-surface)] text-[var(--color-primary-hover)] border border-[var(--color-border-strong)] hover:bg-[var(--color-primary-soft)]"
              onClick={() => resolvePendingEdit('entire')}
            >
              Entire schedule
            </AlertDialogAction>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmReconcileOpen} onOpenChange={setConfirmReconcileOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>This shift repeats into future weeks</AlertDialogTitle>
            <AlertDialogDescription>
              At least one edited block already repeats forward (no end date, or an end date that hasn't passed yet).
              Because a repeating shift is one saved date range rather than separate rows per week, saving updates it
              for the entire remaining range -- there's nothing separate to reconcile for individual future weeks.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Go back</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmReconcileOpen(false)
                doSave()
              }}
            >
              Save for the whole range
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      </div>
        <div className="schedule-tab-sidebar">
          {isAdmin && (
            <div className="schedule-sidebar-panel">
              <h4 style={{ margin: 0 }}>Slot settings</h4>
              <label className="inline-label">
                Default appointment duration
                <select value={defaultDuration} onChange={(e) => setDefaultDuration(Number(e.target.value))}>
                  {DURATION_OPTIONS.map((d) => (
                    <option key={d} value={d}>
                      {d} minutes
                    </option>
                  ))}
                </select>
              </label>
              <p className="muted schedule-sidebar-note">
                Used for this preview only -- actual appointment lengths are set per appointment type (Appointment
                Types tab).
              </p>
              <label className="inline-label">
                Buffer time between appointments
                <select value={bufferMinutes} onChange={(e) => setBufferMinutes(Number(e.target.value))}>
                  {[0, 5, 10, 15, 20, 30].map((b) => (
                    <option key={b} value={b}>
                      {b === 0 ? 'No buffer' : `${b} minutes`}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          <div className="schedule-sidebar-panel">
            <div className="schedule-preview-heading">
              <h4 style={{ margin: 0 }}>Generated slots preview</h4>
              {overallDirty && <span className="draft-badge">Previewing unsaved changes</span>}
            </div>
            <p className="muted" style={{ margin: 0 }}>
              Whole-week preview of the template above.
            </p>
            <div className="schedule-week-preview-grid">
              {DAYS.map((day) => {
                const slots = templateDaySlots(blocksForDay(day), defaultDuration, bufferMinutes)
                return (
                  <div key={day} className="schedule-week-preview-col">
                    <span className="schedule-week-preview-col-name">{DAY_NAMES[day].slice(0, 3)}</span>
                    {slots.length > 0 ? (
                      <div className="schedule-week-preview-col-slots">
                        {slots.map((s, i) => (
                          <span key={i} className="slot-chip-static compact">
                            {s}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="muted schedule-sidebar-note">No hours</span>
                    )}
                  </div>
                )
              })}
            </div>
            <p className="muted schedule-sidebar-note">
              Slots are generated from working hours, appointment duration, and buffer time -- not a guarantee of real
              booking availability (existing appointments and time off aren't excluded here).
            </p>
          </div>
        </div>
      </div>

      {isAdmin && (
        <div className="schedule-save-bar">
          <span className={savedFlash ? 'schedule-save-status success' : 'schedule-save-status'}>
            {savedFlash ? '✓ Saved' : overallDirty ? 'Unsaved changes' : ''}
          </span>
          <div className="schedule-save-bar-actions">
            <button type="button" className="btn-secondary btn" onClick={handleCancel} disabled={cancelDisabled}>
              Cancel
            </button>
            <button type="button" className="btn" onClick={handleSave} disabled={saveDisabled}>
              {busy ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
