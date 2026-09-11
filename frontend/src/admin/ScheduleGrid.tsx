import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  createDoctorSchedule,
  deleteDoctorSchedule,
  getDoctorBlocks,
  getDoctorDepartments,
  getDoctorScheduleAdmin,
  updateDoctorSlotSettings,
} from '../api'
import type { Department, Doctor, DoctorBlockEntry, DoctorScheduleEntry } from '../types'
import { formatDate, formatTimeOfDay } from '../format'
import AdminDatePicker from './AdminDatePicker'
import { TimeCombobox } from '../components/ui/time-combobox'
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
  dateInRange,
  dateToDayOfWeek,
  defaultBreakFor,
  isoDateToday,
  minutesToHHMM,
  mergeEntriesIntoBlocks,
  newBlockKey,
  newPeriodDefaults,
  parseRangeKey,
  paintRange,
  eraseRange,
  rangeKey,
  rowTupleKey,
  blocksToRowPayloads,
  copyBlockToDay,
  templateDaySlots,
  toMinutesSinceMidnight,
  validateBlockBreaks,
  type ScheduleBlock,
} from './doctorSchedule'

const DAYS = [1, 2, 3, 4, 5, 6, 7]
const GRID_HOURS = Array.from({ length: 24 }, (_, i) => i)
const ROW_HEIGHT = 32
const INITIAL_SCROLL_HOUR = 6
const MERGE_THRESHOLD_STORAGE_KEY = 'scheduleGrid.mergeThresholdMinutes'

function hourLabel(hour: number): string {
  return formatTimeOfDay(`${String(hour).padStart(2, '0')}:00`)
}

function loadMergeThreshold(): number {
  try {
    const raw = window.localStorage.getItem(MERGE_THRESHOLD_STORAGE_KEY)
    const parsed = raw ? Number(raw) : NaN
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : DEFAULT_MERGE_GAP_MINUTES
  } catch {
    return DEFAULT_MERGE_GAP_MINUTES
  }
}

// Full-rebuild replacement for the old Working Hours form (ScheduleSection):
// a 7-day x 24-hour drag-to-block weekly grid instead of a Days/Hours/
// Date-range/Department form. See the approved PLAN for the mapping onto
// the unchanged backend (doctor_schedule rows, no new endpoints) and the
// migration approach for existing split-shift/non-standard-time rows
// (doctorSchedule.ts's mergeEntriesIntoBlocks).
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
  // Calendar-first: the monthly view is the default, primary scheduling
  // workspace (select a date, edit it inline, see the live preview). The
  // 7x24 drag grid is kept as an advanced/bulk-editing option, not the
  // entry point -- see the toggle's labels below.
  const [view, setView] = useState<'week' | 'month'>('month')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [savedFlash, setSavedFlash] = useState(false)

  // User-adjustable "does this gap read as a break or as two separate
  // shifts" cutoff for interpreting existing multi-row days on load --
  // a display/editing preference, not real schedule data, so it lives in
  // localStorage rather than a new backend column (see the PLAN's
  // section 6 sign-off).
  const [mergeThresholdMinutes, setMergeThresholdMinutes] = useState(loadMergeThreshold)
  useEffect(() => {
    try {
      window.localStorage.setItem(MERGE_THRESHOLD_STORAGE_KEY, String(mergeThresholdMinutes))
    } catch {
      // best-effort only
    }
  }, [mergeThresholdMinutes])

  const savedBlocks = useMemo(
    () => mergeEntriesIntoBlocks(entries, mergeThresholdMinutes),
    [entries, mergeThresholdMinutes],
  )
  const [draftBlocks, setDraftBlocks] = useState<ScheduleBlock[]>([])
  const [selectedBlockKey, setSelectedBlockKey] = useState<string | null>(null)
  const [rangeOverride, setRangeOverride] = useState<Record<number, string>>({})

  // Defaults applied to a block drawn on a day that has no existing
  // blocks yet -- once a day has a block, further painting on it extends
  // that day's currently-active range instead (see activeRangeForDay).
  const [pendingDepartmentId, setPendingDepartmentId] = useState('')
  const [pendingStartDate, setPendingStartDate] = useState('')
  const [pendingEndDate, setPendingEndDate] = useState('')

  const [defaultDuration, setDefaultDuration] = useState(doctor.default_duration_minutes)
  const [bufferMinutes, setBufferMinutes] = useState(doctor.buffer_minutes)
  const [savedDuration, setSavedDuration] = useState(doctor.default_duration_minutes)
  const [savedBuffer, setSavedBuffer] = useState(doctor.buffer_minutes)
  const slotSettingsDirty = defaultDuration !== savedDuration || bufferMinutes !== savedBuffer

  const [confirmReconcileOpen, setConfirmReconcileOpen] = useState(false)
  const [removeBlockTarget, setRemoveBlockTarget] = useState<string | null>(null)

  // "Copy schedule" -- copies one day's blocks (times, breaks,
  // department) onto other days, staged into the draft like any other
  // edit (see copyBlockToDay's own docstring for why conflicting target
  // days are skipped rather than aborting the whole copy).
  const [showCopyForm, setShowCopyForm] = useState(false)
  const [copySourceDay, setCopySourceDay] = useState('')
  const [copyTargetDays, setCopyTargetDays] = useState<number[]>([])
  const [copyError, setCopyError] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = INITIAL_SCROLL_HOUR * ROW_HEIGHT
  }, [])

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
    setDraftBlocks(mergeEntriesIntoBlocks(entries, mergeThresholdMinutes))
    setSelectedBlockKey(null)
    setRangeOverride({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries])

  function departmentName(id: number | null): string {
    if (id === null) return 'All departments'
    return departments.find((d) => d.id === id)?.name ?? 'All departments'
  }

  function distinctRangesForDay(day: number): string[] {
    return [...new Set(draftBlocks.filter((b) => b.day === day).map((b) => rangeKey(b.startDate, b.endDate)))]
  }

  function defaultRangeForDay(day: number): string {
    const ranges = distinctRangesForDay(day)
    if (ranges.length > 0) return ranges[0]
    return rangeKey(pendingStartDate || null, pendingEndDate || null)
  }

  function activeRangeForDay(day: number): string {
    return rangeOverride[day] ?? defaultRangeForDay(day)
  }

  function blocksForDay(day: number): ScheduleBlock[] {
    const active = activeRangeForDay(day)
    return draftBlocks
      .filter((b) => b.day === day && rangeKey(b.startDate, b.endDate) === active)
      .sort((a, b) => a.startTime.localeCompare(b.startTime))
  }

  function cellFilled(day: number, hour: number): boolean {
    return blocksForDay(day).some(
      (b) => toMinutesSinceMidnight(b.startTime) <= hour * 60 && hour * 60 < toMinutesSinceMidnight(b.endTime),
    )
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

  // -- Drag (mouse) --------------------------------------------------------
  const [drag, setDrag] = useState<{ day: number; mode: 'paint' | 'erase'; anchorHour: number; currentHour: number } | null>(
    null,
  )
  const dragRef = useRef(drag)
  useEffect(() => {
    dragRef.current = drag
  }, [drag])

  function commitDrag(d: { day: number; mode: 'paint' | 'erase'; anchorHour: number; currentHour: number }) {
    const lo = Math.min(d.anchorHour, d.currentHour)
    const hi = Math.max(d.anchorHour, d.currentHour) + 1
    const startTime = minutesToHHMM(lo * 60)
    const endTime = minutesToHHMM(hi * 60)
    const rk = activeRangeForDay(d.day)
    const { startDate, endDate } = parseRangeKey(rk)
    setDraftBlocks((prev) =>
      d.mode === 'paint'
        ? paintRange(prev, d.day, startTime, endTime, startDate, endDate, pendingDepartmentId ? Number(pendingDepartmentId) : null)
        : eraseRange(prev, d.day, startTime, endTime, rk),
    )
    setSelectedBlockKey(null)
  }
  const commitDragRef = useRef(commitDrag)
  useEffect(() => {
    commitDragRef.current = commitDrag
  })
  useEffect(() => {
    function onUp() {
      if (dragRef.current) {
        commitDragRef.current(dragRef.current)
        setDrag(null)
      }
    }
    window.addEventListener('mouseup', onUp)
    return () => window.removeEventListener('mouseup', onUp)
  }, [])

  function onCellMouseDown(day: number, hour: number) {
    if (!isAdmin) return
    setDrag({ day, mode: cellFilled(day, hour) ? 'erase' : 'paint', anchorHour: hour, currentHour: hour })
  }
  function onCellMouseEnter(day: number, hour: number) {
    if (!isAdmin) return
    setDrag((prev) => (prev && prev.day === day ? { ...prev, currentHour: hour } : prev))
  }

  // -- Keyboard path (parallel to drag, same commitDrag) -------------------
  const [focusCell, setFocusCell] = useState({ day: 1, hour: 9 })
  const [keyboardAnchor, setKeyboardAnchor] = useState<{ day: number; hour: number; mode: 'paint' | 'erase' } | null>(null)
  const cellRefs = useRef<Record<string, HTMLButtonElement | null>>({})

  function moveFocus(day: number, hour: number) {
    setFocusCell({ day, hour })
    requestAnimationFrame(() => cellRefs.current[`${day}-${hour}`]?.focus())
  }

  function onCellKeyDown(e: React.KeyboardEvent, day: number, hour: number) {
    if (!isAdmin) return
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      moveFocus(Math.min(day + 1, 7), hour)
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      moveFocus(Math.max(day - 1, 1), hour)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      moveFocus(day, Math.min(hour + 1, 23))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      moveFocus(day, Math.max(hour - 1, 0))
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (!keyboardAnchor || keyboardAnchor.day !== day) {
        setKeyboardAnchor({ day, hour, mode: cellFilled(day, hour) ? 'erase' : 'paint' })
      } else {
        commitDrag({ day, mode: keyboardAnchor.mode, anchorHour: keyboardAnchor.hour, currentHour: hour })
        setKeyboardAnchor(null)
      }
    } else if (e.key === 'Escape') {
      setKeyboardAnchor(null)
    }
  }

  function cellAriaLabel(day: number, hour: number): string {
    const state = cellFilled(day, hour) ? 'available' : 'empty'
    const anchor = keyboardAnchor && keyboardAnchor.day === day && keyboardAnchor.hour === hour ? ', block start set' : ''
    return `${DAY_NAMES[day]}, ${hourLabel(hour)}, ${state}${anchor}`
  }

  // -- Block editing (department / fine-adjust / breaks / range) ----------
  const selectedBlock = draftBlocks.find((b) => b.key === selectedBlockKey) ?? null

  function updateBlock(key: string, mutator: (b: ScheduleBlock) => ScheduleBlock) {
    setDraftBlocks((prev) => prev.map((b) => (b.key === key ? mutator(b) : b)))
  }

  function updateSelectedBlock(mutator: (b: ScheduleBlock) => ScheduleBlock) {
    if (selectedBlockKey) updateBlock(selectedBlockKey, mutator)
  }

  function addBreakToBlock(key: string) {
    updateBlock(key, (b) => ({ ...b, breaks: [...b.breaks, defaultBreakFor(b.startTime, b.endTime)] }))
  }

  function addBreakToSelected() {
    if (selectedBlockKey) addBreakToBlock(selectedBlockKey)
  }

  function removeBlock(key: string) {
    setDraftBlocks((prev) => prev.filter((b) => b.key !== key))
    if (selectedBlockKey === key) setSelectedBlockKey(null)
    setRemoveBlockTarget(null)
  }

  // Monthly day panel's "+ Add another working period" -- creates a new
  // block for that date's weekday, open-ended (applies every such
  // weekday going forward) by default, same convention the weekly grid's
  // own drawn blocks use. The panel's own "Change dates..." lets the
  // admin narrow it to a specific window (or a single day) afterwards.
  // Open-ended by default also keeps "Copy to other days" predictable --
  // a period and its copies share the same range convention, so the
  // calendar doesn't show one weekday recurring into future months while
  // its copies silently don't (or vice versa). This is what lets an
  // admin add hours entirely from the calendar without ever touching the
  // weekly drag grid.
  function addPeriodForDate(dateStr: string, departmentId: number | null = null): string {
    const day = dateToDayOfWeek(dateStr)
    const existing = draftBlocks
      .filter((b) => b.day === day && dateInRange(dateStr, b.startDate, b.endDate))
      .sort((a, b) => a.startTime.localeCompare(b.startTime))
    const lastEnd = existing.length ? existing[existing.length - 1].endTime : undefined
    const { startTime, endTime } = newPeriodDefaults(lastEnd)
    const block: ScheduleBlock = {
      key: newBlockKey(),
      day,
      startTime,
      endTime,
      breaks: [],
      departmentId,
      startDate: null,
      endDate: null,
      sourceIds: [],
    }
    setDraftBlocks((prev) => [...prev, block])
    return block.key
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
      const { startDate, endDate } = parseRangeKey(activeRangeForDay(targetDay))
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

  const selectedBlockError = selectedBlock
    ? !(selectedBlock.startTime < selectedBlock.endTime)
      ? 'End time must be after start time'
      : validateBlockBreaks(selectedBlock.startTime, selectedBlock.endTime, selectedBlock.breaks)
    : null

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
    setSelectedBlockKey(null)
    setRangeOverride({})
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
            {view === 'week'
              ? 'Drag across the grid to draw availability, drag again to erase it.'
              : 'Click a date to see and edit that day’s working hours.'}
          </p>
        </div>
      </div>
      <div className="schedule-header-actions">
        <div className="schedule-view-toggle" role="group" aria-label="Schedule view">
          <button type="button" className={view === 'month' ? 'selected' : ''} onClick={() => setView('month')}>
            Calendar
          </button>
          <button type="button" className={view === 'week' ? 'selected' : ''} onClick={() => setView('week')}>
            Weekly grid (advanced)
          </button>
        </div>
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

      {isAdmin && view === 'week' && (
        <div className="schedule-grid-toolbar">
          <label className="inline-label">
            New block department
            <select value={pendingDepartmentId} onChange={(e) => setPendingDepartmentId(e.target.value)}>
              <option value="">All departments</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <div className="schedule-field-group">
            <span className="field-label">{selectedBlock ? 'Selected block: start date' : 'Start date (new blocks)'}</span>
            <AdminDatePicker
              value={selectedBlock ? selectedBlock.startDate ?? '' : pendingStartDate}
              onChange={(v) => (selectedBlock ? updateSelectedBlock((b) => ({ ...b, startDate: v || null })) : setPendingStartDate(v))}
              label="Pick start date"
            />
          </div>
          <div className="schedule-field-group">
            <span className="field-label">{selectedBlock ? 'Selected block: repeat until' : 'Repeat until (new blocks)'}</span>
            <AdminDatePicker
              value={selectedBlock ? selectedBlock.endDate ?? '' : pendingEndDate}
              onChange={(v) => (selectedBlock ? updateSelectedBlock((b) => ({ ...b, endDate: v || null })) : setPendingEndDate(v))}
              label="Pick end date"
            />
          </div>
          <label className="inline-label">
            Merge gaps under
            <span className="schedule-merge-threshold">
              <input
                type="number"
                min={0}
                step={15}
                value={mergeThresholdMinutes}
                onChange={(e) => setMergeThresholdMinutes(Math.max(0, Number(e.target.value) || 0))}
                aria-label="Merge gap threshold in minutes, applied when interpreting saved schedules as blocks"
              />
              min into a break
            </span>
          </label>
        </div>
      )}

      {view === 'week' && (
      <div className="schedule-grid-wrap">
        <div className="schedule-grid-header-row">
          <div className="schedule-grid-hour-gutter" />
          {DAYS.map((day) => {
            const ranges = distinctRangesForDay(day)
            const active = activeRangeForDay(day)
            return (
              <div key={day} className="schedule-grid-day-header">
                <span>{DAY_NAMES[day]}</span>
                {(ranges.length > 1 || (ranges.length === 1 && ranges[0] !== rangeKey(null, null))) && (
                  <div className="schedule-range-pills" role="group" aria-label={`${DAY_NAMES[day]} date ranges`}>
                    {ranges.map((rk) => {
                      const { startDate, endDate } = parseRangeKey(rk)
                      const label = startDate || endDate ? `${startDate ? formatDate(startDate) : 'Always'}–${endDate ? formatDate(endDate) : 'Always'}` : 'Always'
                      return (
                        <button
                          key={rk}
                          type="button"
                          className={rk === active ? 'selected' : ''}
                          onClick={() => setRangeOverride((prev) => ({ ...prev, [day]: rk }))}
                        >
                          {label}
                        </button>
                      )
                    })}
                  </div>
                )}
                {isAdmin && (pendingStartDate || pendingEndDate) && (
                  <button
                    type="button"
                    className="link"
                    onClick={() =>
                      setRangeOverride((prev) => ({ ...prev, [day]: rangeKey(pendingStartDate || null, pendingEndDate || null) }))
                    }
                  >
                    + add date range
                  </button>
                )}
              </div>
            )
          })}
        </div>

        <div className="schedule-grid-scroll" ref={scrollRef}>
          <div className="schedule-grid-body" style={{ height: 24 * ROW_HEIGHT }}>
            <div className="schedule-grid-hour-gutter">
              {GRID_HOURS.map((h) => (
                <div key={h} className="schedule-grid-hour-label" style={{ height: ROW_HEIGHT }}>
                  {hourLabel(h)}
                </div>
              ))}
            </div>
            {DAYS.map((day) => {
              const dayDrag = drag && drag.day === day ? drag : null
              return (
                <div key={day} className="schedule-grid-day-col" style={{ height: 24 * ROW_HEIGHT }}>
                  {GRID_HOURS.map((h) => (
                    <button
                      key={h}
                      type="button"
                      ref={(el) => {
                        cellRefs.current[`${day}-${h}`] = el
                      }}
                      className="schedule-grid-cell"
                      style={{ height: ROW_HEIGHT }}
                      tabIndex={focusCell.day === day && focusCell.hour === h ? 0 : -1}
                      aria-label={cellAriaLabel(day, h)}
                      onFocus={() => setFocusCell({ day, hour: h })}
                      onMouseDown={() => onCellMouseDown(day, h)}
                      onMouseEnter={() => onCellMouseEnter(day, h)}
                      onKeyDown={(e) => onCellKeyDown(e, day, h)}
                    />
                  ))}

                  {dayDrag && (
                    <div
                      className={dayDrag.mode === 'erase' ? 'schedule-drag-preview erase' : 'schedule-drag-preview'}
                      style={{
                        top: Math.min(dayDrag.anchorHour, dayDrag.currentHour) * ROW_HEIGHT,
                        height: (Math.abs(dayDrag.currentHour - dayDrag.anchorHour) + 1) * ROW_HEIGHT,
                      }}
                    />
                  )}

                  {blocksForDay(day).map((b) => (
                    <div
                      key={b.key}
                      className={b.key === selectedBlockKey ? 'schedule-block selected' : 'schedule-block'}
                      style={{
                        top: (toMinutesSinceMidnight(b.startTime) / 60) * ROW_HEIGHT,
                        height: ((toMinutesSinceMidnight(b.endTime) - toMinutesSinceMidnight(b.startTime)) / 60) * ROW_HEIGHT,
                      }}
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation()
                        if (isAdmin) setSelectedBlockKey(b.key)
                      }}
                    >
                      <div className="schedule-block-label">
                        {formatTimeOfDay(b.startTime)} – {formatTimeOfDay(b.endTime)}
                        <span className="muted"> {departmentName(b.departmentId)}</span>
                      </div>
                      {b.breaks.map((br, i) => (
                        <div
                          key={i}
                          className="schedule-block-break"
                          style={{
                            top: ((toMinutesSinceMidnight(br.start) - toMinutesSinceMidnight(b.startTime)) / 60) * ROW_HEIGHT,
                            height: ((toMinutesSinceMidnight(br.end) - toMinutesSinceMidnight(br.start)) / 60) * ROW_HEIGHT,
                          }}
                          title={`Break ${formatTimeOfDay(br.start)} – ${formatTimeOfDay(br.end)}`}
                        />
                      ))}
                    </div>
                  ))}
                </div>
              )
            })}
          </div>
        </div>
      </div>
      )}

      {view === 'month' && (
        <ScheduleMonthView
          draftBlocks={draftBlocks}
          oneOffBlocks={oneOffBlocks}
          departments={departments}
          defaultDuration={defaultDuration}
          bufferMinutes={bufferMinutes}
          overallDirty={overallDirty}
          isAdmin={isAdmin}
          onAddPeriod={addPeriodForDate}
          onUpdateBlock={updateBlock}
          onAddBreak={addBreakToBlock}
          onRemoveBlock={(key) => setRemoveBlockTarget(key)}
          onCopyFromDate={handleCopyFromDate}
        />
      )}

      {view === 'week' && selectedBlock && isAdmin && (
        <div className="schedule-block-panel">
          <div className="admin-content-header">
            <h4 style={{ margin: 0 }}>
              {DAY_NAMES[selectedBlock.day]} shift
            </h4>
            <button type="button" className="link" onClick={() => setSelectedBlockKey(null)}>
              Close
            </button>
          </div>
          {selectedBlockError && <p className="error">{selectedBlockError}</p>}
          <div className="schedule-field-grid">
            <label className="schedule-field-group">
              <span className="field-label">Start</span>
              <TimeCombobox
                value={selectedBlock.startTime}
                onChange={(v) => updateSelectedBlock((b) => ({ ...b, startTime: v }))}
                durationMinutes={defaultDuration}
                ariaLabel="Block start time"
              />
            </label>
            <label className="schedule-field-group">
              <span className="field-label">End</span>
              <TimeCombobox
                value={selectedBlock.endTime}
                onChange={(v) => updateSelectedBlock((b) => ({ ...b, endTime: v }))}
                durationMinutes={defaultDuration}
                ariaLabel="Block end time"
              />
            </label>
          </div>
          <label className="schedule-field-group">
            <span className="field-label">Department</span>
            <select
              value={selectedBlock.departmentId ?? ''}
              onChange={(e) => updateSelectedBlock((b) => ({ ...b, departmentId: e.target.value ? Number(e.target.value) : null }))}
            >
              <option value="">All departments</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <div className="schedule-field-group schedule-breaks">
            <span className="field-label">Breaks</span>
            {selectedBlock.breaks.map((br, i) => (
              <div key={i} className="schedule-break-row">
                <TimeCombobox
                  value={br.start}
                  onChange={(v) =>
                    updateSelectedBlock((b) => ({
                      ...b,
                      breaks: b.breaks.map((x, xi) => (xi === i ? { ...x, start: v } : x)),
                    }))
                  }
                  durationMinutes={defaultDuration}
                  ariaLabel={`Break ${i + 1} start`}
                />
                <span className="arrow">{'→'}</span>
                <TimeCombobox
                  value={br.end}
                  onChange={(v) =>
                    updateSelectedBlock((b) => ({
                      ...b,
                      breaks: b.breaks.map((x, xi) => (xi === i ? { ...x, end: v } : x)),
                    }))
                  }
                  durationMinutes={defaultDuration}
                  ariaLabel={`Break ${i + 1} end`}
                />
                <button
                  type="button"
                  className="link danger"
                  onClick={() => updateSelectedBlock((b) => ({ ...b, breaks: b.breaks.filter((_, xi) => xi !== i) }))}
                >
                  Remove
                </button>
              </div>
            ))}
            <button type="button" className="link" onClick={addBreakToSelected}>
              + Add a break
            </button>
          </div>
          <button type="button" className="link danger" onClick={() => setRemoveBlockTarget(selectedBlock.key)}>
            Remove this block
          </button>
        </div>
      )}

      <AlertDialog open={removeBlockTarget !== null} onOpenChange={(open) => !open && setRemoveBlockTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this block?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the shift from the draft grid. It isn't persisted until you Save changes.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={() => removeBlockTarget && removeBlock(removeBlockTarget)}>
              Remove
            </AlertDialogAction>
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
