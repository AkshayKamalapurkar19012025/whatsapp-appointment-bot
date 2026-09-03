import { useEffect, useState } from 'react'
import {
  ApiError,
  cancelAdminAppointment,
  createAdminAppointment,
  listAdminAppointments,
  listAllDoctors,
  listAppointmentTypesForDoctor,
  listPatients,
  rescheduleAdminAppointment,
} from '../api'
import type { AdminAppointment, AppointmentType, Doctor, Patient } from '../types'
import { formatDate, formatTime } from '../format'

export default function AppointmentsPanel() {
  const [appointments, setAppointments] = useState<AdminAppointment[]>([])
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [patients, setPatients] = useState<Patient[]>([])
  const [doctorFilter, setDoctorFilter] = useState('')
  const [patientFilter, setPatientFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [reschedulingId, setReschedulingId] = useState<number | null>(null)
  const [rescheduleAt, setRescheduleAt] = useState('')

  function load() {
    setLoading(true)
    setError(null)
    listAdminAppointments({
      doctor_id: doctorFilter ? Number(doctorFilter) : undefined,
      patient_id: patientFilter ? Number(patientFilter) : undefined,
      status: statusFilter || undefined,
    })
      .then(setAppointments)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : 'Could not load appointments'),
      )
      .finally(() => setLoading(false))
  }

  useEffect(load, [doctorFilter, patientFilter, statusFilter])

  useEffect(() => {
    listAllDoctors().then(setDoctors).catch(() => undefined)
    listPatients().then(setPatients).catch(() => undefined)
  }, [])

  async function handleCancel(appointment: AdminAppointment) {
    if (!window.confirm(`Cancel ${appointment.patient_name}'s appointment with ${appointment.doctor_name}?`)) {
      return
    }
    setError(null)
    try {
      await cancelAdminAppointment(appointment.id)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not cancel the appointment')
    }
  }

  function startReschedule(appointment: AdminAppointment) {
    setReschedulingId(appointment.id)
    setRescheduleAt('')
  }

  async function confirmReschedule(appointmentId: number) {
    if (!rescheduleAt) return
    setError(null)
    try {
      // Same IST assumption as doctor blocks/create-appointment below --
      // see types.ts's DoctorBlockEntry docstring.
      await rescheduleAdminAppointment(appointmentId, `${rescheduleAt}:00+05:30`)
      setReschedulingId(null)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reschedule the appointment')
    }
  }

  return (
    <section>
      <h2>Appointments</h2>
      {error && <p className="error">{error}</p>}

      <div className="inline-form wrap">
        <label className="inline-label">
          Doctor
          <select value={doctorFilter} onChange={(e) => setDoctorFilter(e.target.value)}>
            <option value="">All</option>
            {doctors.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-label">
          Patient
          <select value={patientFilter} onChange={(e) => setPatientFilter(e.target.value)}>
            <option value="">All</option>
            {patients.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-label">
          Status
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All</option>
            <option value="BOOKED">Booked</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </label>
        <button type="button" onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? 'Close' : 'Book on behalf of a patient'}
        </button>
      </div>

      {showCreate && (
        <CreateAppointmentForm
          doctors={doctors}
          patients={patients}
          onCreated={() => {
            setShowCreate(false)
            load()
          }}
        />
      )}

      {loading && <p>Loading…</p>}
      {!loading && appointments.length === 0 && <p className="muted">No appointments found.</p>}

      <table className="data-table">
        <thead>
          <tr>
            <th>Patient</th>
            <th>Doctor</th>
            <th>Type</th>
            <th>When</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {appointments.map((a) => (
            <tr key={a.id}>
              <td>
                {a.patient_name}
                <div className="muted">{a.whatsapp_number}</div>
              </td>
              <td>{a.doctor_name}</td>
              <td>{a.appointment_type_name}</td>
              <td>
                {formatDate(a.start_at)} · {formatTime(a.start_at)} – {formatTime(a.end_at)}
              </td>
              <td>{a.status}</td>
              <td>
                {a.status === 'BOOKED' &&
                  (reschedulingId === a.id ? (
                    <span className="inline-form">
                      <input
                        type="datetime-local"
                        value={rescheduleAt}
                        onChange={(e) => setRescheduleAt(e.target.value)}
                      />
                      <button type="button" onClick={() => confirmReschedule(a.id)}>
                        Save
                      </button>
                      <button type="button" className="link" onClick={() => setReschedulingId(null)}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <>
                      <button type="button" className="link" onClick={() => startReschedule(a)}>
                        Reschedule
                      </button>
                      <button type="button" className="link" onClick={() => handleCancel(a)}>
                        Cancel
                      </button>
                    </>
                  ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function CreateAppointmentForm({
  doctors,
  patients,
  onCreated,
}: {
  doctors: Doctor[]
  patients: Patient[]
  onCreated: () => void
}) {
  const [doctorId, setDoctorId] = useState('')
  const [patientId, setPatientId] = useState('')
  const [appointmentTypeId, setAppointmentTypeId] = useState('')
  const [types, setTypes] = useState<AppointmentType[]>([])
  const [startAt, setStartAt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!doctorId) {
      setTypes([])
      return
    }
    listAppointmentTypesForDoctor(Number(doctorId))
      .then(setTypes)
      .catch(() => setTypes([]))
  }, [doctorId])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!doctorId || !patientId || !appointmentTypeId || !startAt) return
    setError(null)
    setBusy(true)
    try {
      // Same IST assumption as doctor blocks -- see types.ts's
      // DoctorBlockEntry docstring.
      await createAdminAppointment(
        Number(doctorId),
        Number(patientId),
        Number(appointmentTypeId),
        `${startAt}:00+05:30`,
      )
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create appointment')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form id="create-appointment-form" className="inline-form wrap" onSubmit={handleSubmit}>
      <label className="inline-label">
        Doctor
        <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)} required>
          <option value="">Choose…</option>
          {doctors.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </label>
      <label className="inline-label">
        Patient
        <select value={patientId} onChange={(e) => setPatientId(e.target.value)} required>
          <option value="">Choose…</option>
          {patients.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <label className="inline-label">
        Appointment type
        <select
          value={appointmentTypeId}
          onChange={(e) => setAppointmentTypeId(e.target.value)}
          required
          disabled={!doctorId}
        >
          <option value="">Choose…</option>
          {types.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} ({t.duration_minutes} min)
            </option>
          ))}
        </select>
      </label>
      <label className="inline-label">
        Start (IST)
        <input
          type="datetime-local"
          value={startAt}
          onChange={(e) => setStartAt(e.target.value)}
          required
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? 'Booking…' : 'Book appointment'}
      </button>
    </form>
  )
}
