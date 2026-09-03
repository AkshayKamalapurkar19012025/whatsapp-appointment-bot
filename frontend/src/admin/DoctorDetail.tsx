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
  listAppointmentTypeCatalog,
  listAppointmentTypesForDoctor,
  listDepartments,
  removeAppointmentTypeFromDoctor,
  removeDoctorFromDepartment,
} from '../api'
import type {
  AppointmentType,
  AppointmentTypeSummary,
  Department,
  Doctor,
  DoctorBlockEntry,
  DoctorScheduleEntry,
} from '../types'
import { formatDate, formatTime } from '../format'

const DAY_NAMES = ['', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export default function DoctorDetail({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  return (
    <div className="doctor-detail">
      <h3>{doctor.name}</h3>
      <DepartmentAssignment doctor={doctor} isAdmin={isAdmin} />
      <ScheduleSection doctor={doctor} isAdmin={isAdmin} />
      <BlocksSection doctor={doctor} />
      <AppointmentTypeAssignment doctor={doctor} isAdmin={isAdmin} />
    </div>
  )
}

// -- Department assignment (ADMIN only) --------------------------------

function DepartmentAssignment({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [assigned, setAssigned] = useState<Department[]>([])
  const [allDepartments, setAllDepartments] = useState<Department[]>([])
  const [selected, setSelected] = useState('')
  const [error, setError] = useState<string | null>(null)

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

  async function handleRemove(departmentId: number) {
    setError(null)
    try {
      await removeDoctorFromDepartment(doctor.id, departmentId)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove department')
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
              <button type="button" className="link" onClick={() => handleRemove(d.id)}>
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
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function load() {
    getDoctorScheduleAdmin(doctor.id)
      .then(setEntries)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load schedule'))
  }

  useEffect(load, [doctor.id])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await createDoctorSchedule(doctor.id, {
        day_of_week: Number(dayOfWeek),
        start_time: startTime,
        end_time: endTime,
        start_date: startDate || null,
        end_date: endDate || null,
      })
      setStartDate('')
      setEndDate('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add schedule')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(scheduleId: number) {
    setError(null)
    try {
      await deleteDoctorSchedule(doctor.id, scheduleId)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove schedule')
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
                {e.start_time} – {e.end_time}
              </td>
              <td>
                {e.start_date || e.end_date
                  ? `${e.start_date ? formatDate(e.start_date) : 'Always'} – ${e.end_date ? formatDate(e.end_date) : 'Always'}`
                  : 'Every week'}
              </td>
              <td>
                {isAdmin && (
                  <button type="button" className="link" onClick={() => handleDelete(e.id)}>
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
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </label>
          <label className="inline-label">
            Until (optional)
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </label>
          <button type="submit" disabled={busy}>
            {busy ? 'Adding…' : 'Add schedule'}
          </button>
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

  function load() {
    getDoctorBlocks(doctor.id)
      .then(setBlocks)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load blocks'))
  }

  useEffect(load, [doctor.id])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
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

  async function handleDelete(blockId: number) {
    setError(null)
    try {
      await deleteDoctorBlock(doctor.id, blockId)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove block')
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
            <button type="button" className="link" onClick={() => handleDelete(b.id)}>
              remove
            </button>
          </li>
        ))}
        {blocks.length === 0 && <li className="muted">No blocks scheduled.</li>}
      </ul>

      <form className="inline-form wrap" onSubmit={handleCreate}>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
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
    </div>
  )
}

// -- Appointment type assignment (ADMIN only) ---------------------------

function AppointmentTypeAssignment({ doctor, isAdmin }: { doctor: Doctor; isAdmin: boolean }) {
  const [assigned, setAssigned] = useState<AppointmentType[]>([])
  const [catalog, setCatalog] = useState<AppointmentTypeSummary[]>([])
  const [selected, setSelected] = useState('')
  const [duration, setDuration] = useState('30')
  const [error, setError] = useState<string | null>(null)

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
      await assignAppointmentTypeToDoctor(doctor.id, Number(selected), Number(duration))
      setSelected('')
      setDuration('30')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not assign appointment type')
    }
  }

  async function handleRemove(appointmentTypeId: number) {
    setError(null)
    try {
      await removeAppointmentTypeFromDoctor(doctor.id, appointmentTypeId)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove appointment type')
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
              <button type="button" className="link" onClick={() => handleRemove(a.id)}>
                remove
              </button>
            )}
          </li>
        ))}
        {assigned.length === 0 && <li className="muted">No appointment types assigned.</li>}
      </ul>

      {isAdmin && unassigned.length > 0 && (
        <form className="inline-form" onSubmit={handleAssign}>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} required>
            <option value="">Add appointment type…</option>
            {unassigned.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <input
            type="number"
            min={1}
            max={480}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            aria-label="Duration in minutes"
          />
          <span className="muted">min</span>
          <button type="submit">Assign</button>
        </form>
      )}
    </div>
  )
}
