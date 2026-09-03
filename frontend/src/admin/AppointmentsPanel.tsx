import { Fragment, useEffect, useState } from 'react'
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
import type { AdminAppointment, AppointmentType, Doctor, Patient, Slot } from '../types'
import { formatDate, formatTime } from '../format'
import AdminSlotPicker from './AdminSlotPicker'

// Duration in minutes between two ISO timestamps -- AdminAppointment
// doesn't carry duration_minutes directly (it's a doctor_appointment_
// types property, not an appointment column), and re-deriving it here is
// simpler than adding a field to the admin listing endpoint for a number
// this component can already compute from what it has.
function durationBetween(startAt: string, endAt: string): number {
  return Math.round((new Date(endAt).getTime() - new Date(startAt).getTime()) / 60000)
}

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
  const [rescheduleSlot, setRescheduleSlot] = useState<Slot | null>(null)
  const [rescheduleBusy, setRescheduleBusy] = useState(false)

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
    setRescheduleSlot(null)
  }

  async function confirmReschedule(appointmentId: number) {
    if (!rescheduleSlot) return
    setError(null)
    setRescheduleBusy(true)
    try {
      await rescheduleAdminAppointment(appointmentId, rescheduleSlot.start_at)
      setReschedulingId(null)
      setRescheduleSlot(null)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reschedule the appointment')
    } finally {
      setRescheduleBusy(false)
    }
  }

  const reschedulingAppointment = appointments.find((a) => a.id === reschedulingId) ?? null

  return (
    <section>
      <div className="admin-content-header">
        <h2>Appointments</h2>
        <button type="button" className="btn" style={{ width: 'auto' }} onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? 'Close' : '+ Book on behalf of a patient'}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="filter-bar">
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

      {loading && (
        <div className="state-block">
          <span className="spinner" aria-hidden="true" />
          Loading appointments…
        </div>
      )}
      {!loading && appointments.length === 0 && (
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            📋
          </span>
          No appointments found.
        </div>
      )}

      {!loading && appointments.length > 0 && (
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
              <Fragment key={a.id}>
                <tr>
                  <td>
                    {a.patient_name}
                    <div className="muted">{a.whatsapp_number}</div>
                  </td>
                  <td>{a.doctor_name}</td>
                  <td>{a.appointment_type_name}</td>
                  <td>
                    {formatDate(a.start_at)} · {formatTime(a.start_at)} – {formatTime(a.end_at)}
                  </td>
                  <td>
                    <span className={`pill status-${a.status.toLowerCase()}`}>{a.status}</span>
                  </td>
                  <td>
                    {a.status === 'BOOKED' && (
                      <>
                        <button
                          type="button"
                          className="link"
                          onClick={() =>
                            reschedulingId === a.id ? setReschedulingId(null) : startReschedule(a)
                          }
                        >
                          {reschedulingId === a.id ? 'Close' : 'Reschedule'}
                        </button>
                        <button type="button" className="link" onClick={() => handleCancel(a)}>
                          Cancel
                        </button>
                      </>
                    )}
                  </td>
                </tr>
                {reschedulingId === a.id && reschedulingAppointment && (
                  <tr>
                    <td colSpan={6}>
                      <div className="detail-section" style={{ margin: '8px 0' }}>
                        <h4>
                          Reschedule {reschedulingAppointment.patient_name} with{' '}
                          {reschedulingAppointment.doctor_name}
                        </h4>
                        <p className="muted">
                          Currently {formatDate(reschedulingAppointment.start_at)} ·{' '}
                          {formatTime(reschedulingAppointment.start_at)} –{' '}
                          {formatTime(reschedulingAppointment.end_at)}
                        </p>
                        <AdminSlotPicker
                          doctorId={reschedulingAppointment.doctor_id}
                          appointmentTypeId={reschedulingAppointment.appointment_type_id}
                          durationMinutes={durationBetween(
                            reschedulingAppointment.start_at,
                            reschedulingAppointment.end_at,
                          )}
                          selectedSlot={rescheduleSlot}
                          onSelect={setRescheduleSlot}
                        />
                        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                          <button
                            type="button"
                            className="btn"
                            style={{ width: 'auto' }}
                            disabled={!rescheduleSlot || rescheduleBusy}
                            onClick={() => confirmReschedule(a.id)}
                          >
                            {rescheduleBusy ? 'Saving…' : 'Confirm new time'}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary btn"
                            style={{ width: 'auto' }}
                            onClick={() => setReschedulingId(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
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
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setAppointmentTypeId('')
    setSelectedSlot(null)
    if (!doctorId) {
      setTypes([])
      return
    }
    listAppointmentTypesForDoctor(Number(doctorId))
      .then(setTypes)
      .catch(() => setTypes([]))
  }, [doctorId])

  useEffect(() => {
    setSelectedSlot(null)
  }, [appointmentTypeId])

  const selectedType = types.find((t) => String(t.id) === appointmentTypeId) ?? null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!doctorId || !patientId || !appointmentTypeId || !selectedSlot) return
    setError(null)
    setBusy(true)
    try {
      await createAdminAppointment(
        Number(doctorId),
        Number(patientId),
        Number(appointmentTypeId),
        selectedSlot.start_at,
      )
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create appointment')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="detail-section" onSubmit={handleSubmit}>
      <h4>Book on behalf of a patient</h4>
      <div className="inline-form wrap">
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
      </div>

      {doctorId && selectedType && (
        <AdminSlotPicker
          doctorId={Number(doctorId)}
          appointmentTypeId={selectedType.id}
          durationMinutes={selectedType.duration_minutes}
          selectedSlot={selectedSlot}
          onSelect={setSelectedSlot}
        />
      )}

      {error && <p className="error">{error}</p>}
      <button type="submit" className="btn" style={{ width: 'auto' }} disabled={busy || !selectedSlot}>
        {busy ? 'Booking…' : 'Book appointment'}
      </button>
    </form>
  )
}
