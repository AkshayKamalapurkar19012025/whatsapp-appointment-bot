import { useEffect, useState } from 'react'
import {
  ApiError,
  assignAppointmentTypeToDoctor,
  assignDoctorToDepartment,
  createDoctorBlock,
  createDoctorSchedule,
  deleteDoctorBlock,
  deleteDoctorSchedule,
  getDoctorBlocks,
  getDoctorDepartments,
  getDoctorScheduleAdmin,
  listAdminAppointments,
  listAppointmentTypeCatalog,
  listAppointmentTypesForDoctor,
  listDepartments,
  removeAppointmentTypeFromDoctor,
  removeDoctorFromDepartment,
} from '../api'
import type {
  AdminAppointment,
  AppointmentType,
  AppointmentTypeSummary,
  Department,
  Doctor,
  DoctorBlockEntry,
  DoctorScheduleEntry,
} from '../types'
import { formatDate, formatTime, formatTimeOfDay } from '../format'
import AdminDatePicker from './AdminDatePicker'
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

const DAY_NAMES = ['', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

// Does `dayOfWeek` (1=Monday..7=Sunday) occur at least once between
// startDate and endDate (inclusive)? A schedule row bounded to a date
// range that never actually contains its own day-of-week can be created
// today with no error and then silently never produce a single bookable
// day -- e.g. "Monday, 10 Sep - 11 Sep" when the 10th and 11th are a
// Wednesday and Thursday. An open-ended side (no start or no end) always
// eventually reaches every weekday, so only a fully-bounded range needs
// checking.
function dayOfWeekOccursInRange(dayOfWeek: number, startDate: string, endDate: string): boolean {
  const start = new Date(`${startDate}T00:00:00`)
  const end = new Date(`${endDate}T00:00:00`)
  if (start > end) return true // a different problem (end before start) -- not this check's job
  const jsTargetDay = dayOfWeek % 7 // Date.getDay(): Sunday=0..Saturday=6; ours: Monday=1..Sunday=7
  const cursor = new Date(start)
  while (cursor <= end) {
    if (cursor.getDay() === jsTargetDay) return true
    cursor.setDate(cursor.getDate() + 1)
  }
  return false
}

// Duration options offered when assigning an appointment type to a
// doctor. 0 is deliberately excluded even though it would otherwise be
// the natural first step in a 0-60 range: both the API
// (app/api/doctor_appointment_types.py's Field(gt=0, le=480)) and the DB
// itself (doctor_appointment_types' chk_doctor_appointment_types_duration
// CHECK (duration_minutes > 0)) already reject it, so offering it here
// would just be a guaranteed-to-fail click.
const DURATION_OPTIONS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]

function toMinutesSinceMidnight(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

function toHHMM(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

// Illustrative only -- a straight stride from start to end at the given
// duration, with no doctor-block/existing-appointment exclusion (that's
// what the real /api/availability engine does once a patient/staff
// member is booking against a specific date). This exists purely so
// Start -> End -> Duration -> Generated Slots is visible while setting
// up the recurring schedule, before any date-specific booking exists.
function previewSlots(startTime: string, endTime: string, durationMinutes: number): string[] {
  const start = toMinutesSinceMidnight(startTime)
  const end = toMinutesSinceMidnight(endTime)
  if (!(end > start) || durationMinutes <= 0) return []
  const slots: string[] = []
  for (let t = start; t + durationMinutes <= end; t += durationMinutes) {
    slots.push(`${formatTimeOfDay(toHHMM(t))} – ${formatTimeOfDay(toHHMM(t + durationMinutes))}`)
  }
  return slots
}

type DetailTab = 'upcoming' | 'schedule' | 'blocks' | 'departments' | 'types'

const DETAIL_TABS: { key: DetailTab; label: string }[] = [
  { key: 'upcoming', label: 'Upcoming' },
  { key: 'schedule', label: 'Schedule' },
  { key: 'blocks', label: 'Time off' },
  { key: 'departments', label: 'Departments' },
  { key: 'types', label: 'Appointment types' },
]

// Tabbed rather than every section stacked one after another -- with
// the schedule form's break list and the upcoming-appointments table
// both on screen at once, the page had grown long enough that it read
// as cluttered/confusing (direct feedback). Each section keeps its own
// state and reloads from the server when its tab is shown; nothing here
// changes what any section does, only how many of them are visible at
// once.
export default function DoctorDetail({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [tab, setTab] = useState<DetailTab>('upcoming')

  return (
    <div className="doctor-detail">
      <h3>{doctor.name}</h3>

      <div className="tabs">
        {DETAIL_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={t.key === tab ? 'tab active' : 'tab'}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'upcoming' && <UpcomingAppointmentsSection doctor={doctor} />}
      {tab === 'schedule' && <ScheduleSection doctor={doctor} isAdmin={isAdmin} />}
      {tab === 'blocks' && <BlocksSection doctor={doctor} />}
      {tab === 'departments' && <DepartmentAssignment doctor={doctor} isAdmin={isAdmin} />}
      {tab === 'types' && <AppointmentTypeAssignment doctor={doctor} isAdmin={isAdmin} />}
    </div>
  )
}

// -- This doctor's upcoming appointments (ADMIN or STAFF) ---------------
// Scoped to doctor.id server-side via the same /appointments listing
// AppointmentsPanel's own Upcoming tab uses; "upcoming" here means the
// same thing it does there -- booked and not yet started (no separate
// COMPLETED status, see migrations/0001_baseline_schema.sql, so a past
// BOOKED appointment falls out of Upcoming on its own).

function UpcomingAppointmentsSection({ doctor }: { doctor: Doctor }) {
  const [appointments, setAppointments] = useState<AdminAppointment[]>([])
  const [searchText, setSearchText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  function load() {
    setLoading(true)
    setError(null)
    listAdminAppointments({ doctor_id: doctor.id, status: 'BOOKED' })
      .then(setAppointments)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load appointments'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [doctor.id])

  const searchNeedle = searchText.trim().toLowerCase()
  // Same "is it still in the future" check AppointmentsPanel's own
  // Upcoming tab already does; Date.now() here only ever changes what
  // already-stale data looks like on the next real render, never
  // mid-render.
  const upcoming = appointments
    // eslint-disable-next-line react/purity
    .filter((a) => new Date(a.start_at).getTime() >= Date.now())
    .filter(
      (a) =>
        !searchNeedle ||
        a.patient_name.toLowerCase().includes(searchNeedle) ||
        a.whatsapp_number.toLowerCase().includes(searchNeedle),
    )
    .sort((a, b) => a.start_at.localeCompare(b.start_at))

  return (
    <div className="detail-section">
      <h4>Upcoming appointments</h4>
      {error && <p className="error">{error}</p>}

      <div className="inline-form">
        <label className="inline-label">
          Search patient
          <input
            type="search"
            placeholder="Name or number"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </label>
      </div>

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading…
        </div>
      )}
      {!loading && upcoming.length === 0 && (
        <p className="muted">
          {appointments.length === 0 ? 'No upcoming appointments.' : 'No appointments match your search.'}
        </p>
      )}
      {!loading && upcoming.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Patient</th>
              <th>Type</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {upcoming.map((a) => (
              <tr key={a.id}>
                <td>
                  {a.patient_name}
                  <div className="muted">{a.whatsapp_number}</div>
                </td>
                <td>{a.appointment_type_name}</td>
                <td>
                  {formatDate(a.start_at)} · {formatTime(a.start_at)} – {formatTime(a.end_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// -- Department assignment (ADMIN only) --------------------------------

function DepartmentAssignment({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [assigned, setAssigned] = useState<Department[]>([])
  const [allDepartments, setAllDepartments] = useState<Department[]>([])
  const [selected, setSelected] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [removeTarget, setRemoveTarget] = useState<Department | null>(null)

  function load() {
    Promise.all([getDoctorDepartments(doctor.id), listDepartments()])
      .then(([assignedList, all]) => {
        setAssigned(assignedList)
        setAllDepartments(all)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load departments'))
  }

  useEffect(load, [doctor.id])

  const unassigned = allDepartments.filter((d) => !assigned.some((a) => a.id === d.id))

  async function handleAssign(e: React.FormEvent) {
    e.preventDefault()
    if (!selected) return
    setError(null)
    try {
      await assignDoctorToDepartment(doctor.id, Number(selected))
      setSelected('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not assign department')
    }
  }

  async function confirmRemove() {
    if (!removeTarget) return
    setError(null)
    try {
      await removeDoctorFromDepartment(doctor.id, removeTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove department')
    } finally {
      setRemoveTarget(null)
    }
  }

  return (
    <div className="detail-section">
      <h4>Departments</h4>
      {error && <p className="error">{error}</p>}
      <ul className="tag-list">
        {assigned.map((d) => (
          <li key={d.id}>
            {d.name}
            {isAdmin && (
              <button type="button" className="link" onClick={() => setRemoveTarget(d)}>
                remove
              </button>
            )}
          </li>
        ))}
        {assigned.length === 0 && <li className="muted">Not assigned to any department.</li>}
      </ul>
      {isAdmin && unassigned.length > 0 && (
        <form className="inline-form" onSubmit={handleAssign}>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} required>
            <option value="">Add to department…</option>
            {unassigned.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <button type="submit">Assign</button>
        </form>
      )}

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove department?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget && `Remove ${doctor.name} from ${removeTarget.name}?`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// -- Recurring weekly schedule (ADMIN only, WEB P7 date ranges) --------

function ScheduleSection({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [entries, setEntries] = useState<DoctorScheduleEntry[]>([])
  const [dayOfWeek, setDayOfWeek] = useState('1')
  const [startTime, setStartTime] = useState('09:00')
  const [endTime, setEndTime] = useState('17:00')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [breaks, setBreaks] = useState<{ start: string; end: string }[]>([])
  const [previewDuration, setPreviewDuration] = useState(30)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [removeTarget, setRemoveTarget] = useState<DoctorScheduleEntry | null>(null)

  function load() {
    getDoctorScheduleAdmin(doctor.id)
      .then(setEntries)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load schedule'))
  }

  useEffect(load, [doctor.id])

  function addBreak() {
    setBreaks((prev) => [...prev, { start: '13:00', end: '14:00' }])
  }

  function updateBreak(index: number, field: 'start' | 'end', value: string) {
    setBreaks((prev) => prev.map((b, i) => (i === index ? { ...b, [field]: value } : b)))
  }

  function removeBreak(index: number) {
    setBreaks((prev) => prev.filter((_, i) => i !== index))
  }

  const sortedBreaks = [...breaks].sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0))

  // There is no "break" field on the backend -- a doctor_schedule row is
  // just one contiguous start_time/end_time range (see
  // app/api/doctor_schedule.py). Any number of breaks is expressed the
  // same way the availability engine already supports it: separate,
  // non-overlapping rows for the same day (app/services/
  // availability_engine.py iterates every matching row and generates
  // slots per-row, so a gap between rows naturally has no slots in it).
  // This form just saves the admin from having to work that split out
  // by hand and submit it as several manual "Add schedule" round trips.
  function validateBreaks(): string | null {
    for (const b of sortedBreaks) {
      if (!(b.start < b.end)) return "Each break's end time must be after its start time"
      if (!(startTime < b.start) || !(b.end < endTime)) {
        return 'Breaks must fall entirely within the working hours'
      }
    }
    for (let i = 1; i < sortedBreaks.length; i++) {
      if (sortedBreaks[i].start < sortedBreaks[i - 1].end) {
        return 'Breaks cannot overlap each other'
      }
    }
    return null
  }

  // The working hours split around zero or more sorted, non-overlapping
  // breaks -- e.g. 09:00-17:00 with breaks at 11:00-11:15 and
  // 13:00-14:00 becomes [09:00-11:00, 11:15-13:00, 14:00-17:00]. With no
  // breaks this is just the one original [startTime, endTime] segment.
  function scheduleSegments(): { start: string; end: string }[] {
    const segments: { start: string; end: string }[] = []
    let cursor = startTime
    for (const b of sortedBreaks) {
      segments.push({ start: cursor, end: b.start })
      cursor = b.end
    }
    segments.push({ start: cursor, end: endTime })
    return segments
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (startDate && endDate && !dayOfWeekOccursInRange(Number(dayOfWeek), startDate, endDate)) {
      setError(
        `${DAY_NAMES[Number(dayOfWeek)]} doesn't fall between ${formatDate(startDate)} and ${formatDate(endDate)}, ` +
          'so this schedule would never actually apply. Pick a date range that includes at least one ' +
          `${DAY_NAMES[Number(dayOfWeek)]}, or choose a different day of week.`,
      )
      return
    }

    const breaksError = validateBreaks()
    if (breaksError) {
      setError(breaksError)
      return
    }

    const segments = scheduleSegments()
    setBusy(true)
    try {
      // Submitted as N separate rows, not one transaction -- if a later
      // call fails (e.g. it overlaps something an earlier call's
      // success didn't), say so plainly with how far it got and reload
      // so the list shows what's actually there, rather than silently
      // leaving a half-added schedule the admin doesn't know about.
      for (let i = 0; i < segments.length; i++) {
        try {
          await createDoctorSchedule(doctor.id, {
            day_of_week: Number(dayOfWeek),
            start_time: segments[i].start,
            end_time: segments[i].end,
            start_date: startDate || null,
            end_date: endDate || null,
          })
        } catch (err) {
          if (i > 0) {
            setError(
              `Added ${i} of ${segments.length} segments, but could not add the segment starting at ` +
                `${formatTimeOfDay(segments[i].start)}: ${err instanceof ApiError ? err.message : 'unknown error'}`,
            )
            load()
            return
          }
          throw err
        }
      }
      setStartDate('')
      setEndDate('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add schedule')
    } finally {
      setBusy(false)
    }
  }

  const previewSlotsList = scheduleSegments().flatMap((seg) =>
    previewSlots(seg.start, seg.end, previewDuration),
  )

  async function confirmRemove() {
    if (!removeTarget) return
    setError(null)
    try {
      await deleteDoctorSchedule(doctor.id, removeTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove schedule')
    } finally {
      setRemoveTarget(null)
    }
  }

  return (
    <div className="detail-section">
      <h4>Recurring weekly schedule</h4>
      {error && <p className="error">{error}</p>}

      <table className="data-table">
        <thead>
          <tr>
            <th>Day</th>
            <th>Hours</th>
            <th>Date range</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td>{DAY_NAMES[e.day_of_week]}</td>
              <td>
                {formatTimeOfDay(e.start_time)} – {formatTimeOfDay(e.end_time)}
              </td>
              <td>
                {e.start_date || e.end_date
                  ? `${e.start_date ? formatDate(e.start_date) : 'Always'} – ${e.end_date ? formatDate(e.end_date) : 'Always'}`
                  : 'Every week'}
              </td>
              <td>
                {isAdmin && (
                  <button type="button" className="link" onClick={() => setRemoveTarget(e)}>
                    remove
                  </button>
                )}
              </td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                No recurring schedule set.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this schedule?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget &&
                `Remove the ${DAY_NAMES[removeTarget.day_of_week]} ${formatTimeOfDay(removeTarget.start_time)}–${formatTimeOfDay(removeTarget.end_time)} schedule? Patients will no longer be able to book into this slot.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {isAdmin && (
        <form className="inline-form wrap" onSubmit={handleCreate}>
          <select value={dayOfWeek} onChange={(e) => setDayOfWeek(e.target.value)}>
            {DAY_NAMES.slice(1).map((name, i) => (
              <option key={i + 1} value={i + 1}>
                {name}
              </option>
            ))}
          </select>
          <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} required />
          <input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} required />
          <label className="inline-label">
            From (optional)
            <AdminDatePicker value={startDate} onChange={setStartDate} label="Pick start date" />
          </label>
          <label className="inline-label">
            Until (optional)
            <AdminDatePicker value={endDate} onChange={setEndDate} label="Pick end date" />
          </label>
          <button type="submit" disabled={busy}>
            {busy ? 'Adding…' : 'Add schedule'}
          </button>

          <div style={{ width: '100%' }}>
            {breaks.map((b, i) => (
              <div key={i} className="inline-form wrap" style={{ marginTop: 0 }}>
                <label className="inline-label">
                  Break {i + 1} start
                  <input
                    type="time"
                    value={b.start}
                    onChange={(e) => updateBreak(i, 'start', e.target.value)}
                    required
                  />
                </label>
                <label className="inline-label">
                  Break {i + 1} end
                  <input
                    type="time"
                    value={b.end}
                    onChange={(e) => updateBreak(i, 'end', e.target.value)}
                    required
                  />
                </label>
                <button type="button" className="link danger" onClick={() => removeBreak(i)}>
                  Remove break
                </button>
              </div>
            ))}
            <button type="button" className="link" onClick={addBreak}>
              + Add a break (lunch, or any other daily gap)
            </button>
          </div>

          <div className="schedule-preview">
            <strong>{formatTimeOfDay(startTime)}</strong>
            <span className="arrow">→</span>
            <strong>{formatTimeOfDay(endTime)}</strong>
            {sortedBreaks.length > 0 && (
              <span className="muted">
                (minus {sortedBreaks.map((b) => `${formatTimeOfDay(b.start)}–${formatTimeOfDay(b.end)}`).join(', ')})
              </span>
            )}
            <span className="arrow">÷</span>
            <select
              aria-label="Preview appointment length"
              value={previewDuration}
              onChange={(e) => setPreviewDuration(Number(e.target.value))}
              style={{ width: 'auto', marginBottom: 0 }}
            >
              {[15, 20, 30, 45, 60].map((d) => (
                <option key={d} value={d}>
                  {d} min appt
                </option>
              ))}
            </select>
            <span className="arrow">=</span>
            <span className="muted">generated slots preview</span>
            <div className="schedule-preview-slots">
              {previewSlotsList.map((s) => (
                <span key={s} className="slot-chip-static">
                  {s}
                </span>
              ))}
              {previewSlotsList.length === 0 && (
                <span className="muted">End time must be after start time to preview slots.</span>
              )}
            </div>
          </div>
        </form>
      )}
    </div>
  )
}

// -- One-off blocks (ADMIN or STAFF) ------------------------------------
// Entered/shown in Asia/Kolkata terms -- see types.ts's DoctorBlockEntry
// docstring for why (no API currently exposes/sets a doctor's timezone,
// and every doctor created through this admin UI is Asia/Kolkata by the
// doctors table's own column default).

function BlocksSection({ doctor }: { doctor: Doctor }) {
  const [blocks, setBlocks] = useState<DoctorBlockEntry[]>([])
  const [date, setDate] = useState('')
  const [startTime, setStartTime] = useState('09:00')
  const [endTime, setEndTime] = useState('10:00')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [removeTarget, setRemoveTarget] = useState<DoctorBlockEntry | null>(null)

  function load() {
    getDoctorBlocks(doctor.id)
      .then(setBlocks)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load blocks'))
  }

  useEffect(load, [doctor.id])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    // The date field is no longer a native <input required> (it's the
    // calendar-panel AdminDatePicker below, which has no built-in HTML
    // validation), so this guard replaces what `required` used to do.
    if (!date) {
      setError('Choose a date for this block')
      return
    }
    setBusy(true)
    try {
      await createDoctorBlock(
        doctor.id,
        `${date}T${startTime}:00+05:30`,
        `${date}T${endTime}:00+05:30`,
        reason,
      )
      setReason('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add block')
    } finally {
      setBusy(false)
    }
  }

  async function confirmRemove() {
    if (!removeTarget) return
    setError(null)
    try {
      await deleteDoctorBlock(doctor.id, removeTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove block')
    } finally {
      setRemoveTarget(null)
    }
  }

  return (
    <div className="detail-section">
      <h4>One-off unavailability (IST)</h4>
      {error && <p className="error">{error}</p>}

      <ul className="tag-list">
        {blocks.map((b) => (
          <li key={b.id}>
            {formatDate(b.start_at)} {formatTime(b.start_at)} – {formatTime(b.end_at)}: {b.reason}
            <button type="button" className="link" onClick={() => setRemoveTarget(b)}>
              remove
            </button>
          </li>
        ))}
        {blocks.length === 0 && <li className="muted">No blocks scheduled.</li>}
      </ul>

      <form className="inline-form wrap" onSubmit={handleCreate}>
        <AdminDatePicker value={date} onChange={setDate} label={date ? 'Change date' : 'Pick a date'} />
        <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} required />
        <input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} required />
        <input
          placeholder="Reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          required
        />
        <button type="submit" disabled={busy}>
          {busy ? 'Adding…' : 'Add block'}
        </button>
      </form>

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this block?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget &&
                `Remove the ${formatDate(removeTarget.start_at)} ${formatTime(removeTarget.start_at)}–${formatTime(removeTarget.end_at)} block (${removeTarget.reason})? That time will become bookable again.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// -- Appointment type assignment (ADMIN only) ---------------------------

function AppointmentTypeAssignment({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [assigned, setAssigned] = useState<AppointmentType[]>([])
  const [catalog, setCatalog] = useState<AppointmentTypeSummary[]>([])
  const [selected, setSelected] = useState('')
  const [duration, setDuration] = useState(30)
  const [error, setError] = useState<string | null>(null)
  const [removeTarget, setRemoveTarget] = useState<AppointmentType | null>(null)

  function load() {
    Promise.all([listAppointmentTypesForDoctor(doctor.id), listAppointmentTypeCatalog()])
      .then(([assignedList, all]) => {
        setAssigned(assignedList)
        setCatalog(all)
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : 'Could not load appointment types'),
      )
  }

  useEffect(load, [doctor.id])

  const unassigned = catalog.filter((c) => !assigned.some((a) => a.id === c.id))

  async function handleAssign(e: React.FormEvent) {
    e.preventDefault()
    if (!selected) return
    setError(null)
    try {
      await assignAppointmentTypeToDoctor(doctor.id, Number(selected), duration)
      setSelected('')
      setDuration(30)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not assign appointment type')
    }
  }

  async function confirmRemove() {
    if (!removeTarget) return
    setError(null)
    try {
      await removeAppointmentTypeFromDoctor(doctor.id, removeTarget.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove appointment type')
    } finally {
      setRemoveTarget(null)
    }
  }

  return (
    <div className="detail-section">
      <h4>Appointment types offered</h4>
      {error && <p className="error">{error}</p>}

      <ul className="tag-list">
        {assigned.map((a) => (
          <li key={a.id}>
            {a.name} ({a.duration_minutes} min)
            {isAdmin && (
              <button type="button" className="link" onClick={() => setRemoveTarget(a)}>
                remove
              </button>
            )}
          </li>
        ))}
        {assigned.length === 0 && <li className="muted">No appointment types assigned.</li>}
      </ul>

      {isAdmin && unassigned.length > 0 && (
        <form className="inline-form wrap" onSubmit={handleAssign}>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} required>
            <option value="">Add appointment type…</option>
            {unassigned.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <button type="submit" style={{ width: 'auto' }}>
            Assign
          </button>

          <div style={{ width: '100%' }}>
            <span className="field-label">Consultation duration</span>
            <div className="duration-stepper" role="group" aria-label="Consultation duration in minutes">
              {DURATION_OPTIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  className={d === duration ? 'selected' : ''}
                  onClick={() => setDuration(d)}
                >
                  {d} min
                </button>
              ))}
            </div>
          </div>
        </form>
      )}

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove appointment type?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget && `Stop offering ${removeTarget.name} for ${doctor.name}?`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={confirmRemove}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
