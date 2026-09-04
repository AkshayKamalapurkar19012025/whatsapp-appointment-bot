import { useEffect, useState } from 'react'
import { CheckCircle } from '@phosphor-icons/react'
import {
  ApiError,
  createAdminAppointment,
  createPatientAdmin,
  listAllDoctors,
  listAppointmentTypesForDoctor,
  listPatients,
} from '../api'
import type { AppointmentType, Doctor, Patient, Slot } from '../types'
import AdminSlotPicker from './AdminSlotPicker'
import PhoneInput from '../PhoneInput'

const NEW_PATIENT_VALUE = '__new__'

// A dedicated page for the one thing it does: put a new appointment on
// the calendar for a patient who isn't booking it themselves (phone
// call, walk-in, etc.) -- kept separate from the Appointments section
// (which lists/filters/reschedules/cancels *existing* appointments) so
// the two nav items land somewhere visibly different instead of the
// same list with a form silently toggled open inside it. Direct
// feedback: the two used to look identical because "Book Appointment"
// just flipped a boolean on the Appointments page.
export default function BookAppointmentPanel({ onViewAppointments }: { onViewAppointments: () => void }) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [patients, setPatients] = useState<Patient[]>([])
  const [doctorId, setDoctorId] = useState('')
  const [patientId, setPatientId] = useState('')
  const [appointmentTypeId, setAppointmentTypeId] = useState('')
  const [types, setTypes] = useState<AppointmentType[]>([])
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)
  const [newPatientName, setNewPatientName] = useState('')
  const [newPatientPhone, setNewPatientPhone] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [justBooked, setJustBooked] = useState<{ doctorName: string; patientName: string; slot: Slot } | null>(null)

  useEffect(() => {
    listAllDoctors().then(setDoctors).catch(() => undefined)
    listPatients().then(setPatients).catch(() => undefined)
  }, [])

  const isNewPatient = patientId === NEW_PATIENT_VALUE

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
  const selectedDoctor = doctors.find((d) => String(d.id) === doctorId) ?? null

  function resetForm() {
    setDoctorId('')
    setPatientId('')
    setAppointmentTypeId('')
    setSelectedSlot(null)
    setNewPatientName('')
    setNewPatientPhone('')
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!doctorId || !patientId || !appointmentTypeId || !selectedSlot || !selectedDoctor) return
    if (isNewPatient && (!newPatientName.trim() || !newPatientPhone)) return
    setError(null)
    setBusy(true)
    try {
      let targetPatientId = Number(patientId)
      let targetPatientName = patients.find((p) => String(p.id) === patientId)?.name ?? ''
      if (isNewPatient) {
        const created = await createPatientAdmin(newPatientName.trim(), newPatientPhone)
        setPatients((prev) => [...prev, created])
        targetPatientId = created.id
        targetPatientName = created.name
      }
      await createAdminAppointment(Number(doctorId), targetPatientId, Number(appointmentTypeId), selectedSlot.start_at)
      setJustBooked({ doctorName: selectedDoctor.name, patientName: targetPatientName, slot: selectedSlot })
      resetForm()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : isNewPatient
            ? 'Could not create the new patient'
            : 'Could not create appointment',
      )
    } finally {
      setBusy(false)
    }
  }

  if (justBooked) {
    return (
      <section>
        <h2>Book Appointment</h2>
        <div className="state-block empty">
          <span className="state-icon" aria-hidden="true">
            <CheckCircle size={28} weight="light" />
          </span>
          <p>
            Booked {justBooked.patientName} with {justBooked.doctorName}.
          </p>
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginTop: 8 }}>
            <button type="button" className="btn" style={{ width: 'auto' }} onClick={() => setJustBooked(null)}>
              Book another
            </button>
            <button type="button" className="btn-secondary btn" style={{ width: 'auto' }} onClick={onViewAppointments}>
              View appointments
            </button>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section>
      <h2>Book Appointment</h2>
      <p className="muted">Put a new appointment on the calendar for a patient -- phone call, walk-in, or on their behalf.</p>

      <form className="detail-section" onSubmit={handleSubmit}>
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
              <option value={NEW_PATIENT_VALUE}>+ New patient…</option>
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

        {isNewPatient && (
          <div className="inline-form wrap" style={{ marginTop: 0 }}>
            <label className="inline-label">
              New patient's name
              <input
                placeholder="Full name"
                value={newPatientName}
                onChange={(e) => setNewPatientName(e.target.value)}
                required
              />
            </label>
            <label className="inline-label">
              Mobile number
              <PhoneInput value={newPatientPhone} onChange={setNewPatientPhone} />
            </label>
          </div>
        )}

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
        <button
          type="submit"
          className="btn"
          style={{ width: 'auto' }}
          disabled={busy || !selectedSlot || (isNewPatient && (!newPatientName.trim() || !newPatientPhone))}
        >
          {busy ? 'Booking…' : 'Book appointment'}
        </button>
      </form>
    </section>
  )
}
