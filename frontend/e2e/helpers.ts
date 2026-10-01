import type { APIRequestContext } from '@playwright/test'

// Backend under test, hit directly (not through Vite's dev-server
// proxy) -- these are seeding calls, not the thing under test, so
// there's no reason to route them through the same proxy the browser
// itself uses. Point playwright.config.ts's baseURL at the frontend;
// this stays separate on purpose.
export const API_BASE = 'http://127.0.0.1:8000/api'

// There's no self-service staff signup in this app (see
// app/services/staff_management.py's module docstring) -- a fixed
// e2e-admin account is created once, directly via create_staff_account,
// by whoever provisions the e2e database (see frontend/e2e/README.md).
// Every spec logs in as this account, the same way every backend test
// logs in via tests/helpers.py's create_admin_and_get_headers instead
// of a signup flow that doesn't exist.
const E2E_ADMIN_USERNAME = 'e2e-admin'
const E2E_ADMIN_PASSWORD = 'e2e-password-123'

export function uniq(prefix: string): string {
  return `${prefix} ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

export async function loginAdmin(request: APIRequestContext): Promise<string> {
  const res = await request.post(`${API_BASE}/auth/staff/login`, {
    data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
  })
  if (!res.ok()) {
    throw new Error(`e2e-admin login failed (${res.status()}): ${await res.text()}`)
  }
  const body = await res.json()
  return body.session_token as string
}

function authed(token: string) {
  return { Authorization: `Bearer ${token}` }
}

export async function createDepartment(request: APIRequestContext, token: string, name: string) {
  const res = await request.post(`${API_BASE}/departments`, { headers: authed(token), data: { name } })
  if (!res.ok()) throw new Error(`createDepartment failed: ${await res.text()}`)
  return res.json()
}

export async function createAppointmentType(request: APIRequestContext, token: string, name: string) {
  const res = await request.post(`${API_BASE}/appointment-types`, { headers: authed(token), data: { name } })
  if (!res.ok()) throw new Error(`createAppointmentType failed: ${await res.text()}`)
  return res.json()
}

export async function createDoctor(request: APIRequestContext, token: string, name: string, specialization = 'General Medicine') {
  const res = await request.post(`${API_BASE}/doctors`, { headers: authed(token), data: { name, specialization } })
  if (!res.ok()) throw new Error(`createDoctor failed: ${await res.text()}`)
  return res.json()
}

export async function assignDoctorToDepartment(request: APIRequestContext, token: string, doctorId: number, departmentId: number) {
  const res = await request.post(`${API_BASE}/doctors/${doctorId}/departments/${departmentId}`, { headers: authed(token) })
  if (!res.ok()) throw new Error(`assignDoctorToDepartment failed: ${await res.text()}`)
}

export async function assignAppointmentType(
  request: APIRequestContext,
  token: string,
  doctorId: number,
  appointmentTypeId: number,
  opts: { duration_minutes?: number; consultation_fee?: number } = {},
) {
  const res = await request.post(`${API_BASE}/doctors/${doctorId}/appointment-types/${appointmentTypeId}`, {
    headers: authed(token),
    data: { duration_minutes: opts.duration_minutes ?? 20, consultation_fee: opts.consultation_fee ?? 500 },
  })
  if (!res.ok()) throw new Error(`assignAppointmentType failed: ${await res.text()}`)
  return res.json()
}

// Every day of the week, 00:00-23:45 -- an e2e run's "now" is whatever
// moment it happens to execute at, so a narrow real-clinic-hours
// schedule (e.g. 09:00-17:00) would make bookAndCheckIn below flaky
// depending on time of day. This is seed data for driving the UI under
// test, not a realistic clinic schedule.
export async function setFullAvailability(request: APIRequestContext, token: string, doctorId: number) {
  for (let day = 1; day <= 7; day++) {
    const res = await request.post(`${API_BASE}/doctors/${doctorId}/schedule`, {
      headers: authed(token),
      data: { day_of_week: day, start_time: '00:00', end_time: '23:45' },
    })
    if (!res.ok()) throw new Error(`setFullAvailability failed: ${await res.text()}`)
  }
}

export async function createPatient(
  request: APIRequestContext,
  token: string,
  name: string,
  whatsapp_number: string,
  // date_of_birth/gender are optional on PatientCreate itself (fast
  // walk-in registration must never be blocked on them), so they stay
  // optional here too -- only the specs that actually assert on the
  // rendered demographics line pass them.
  opts: { date_of_birth?: string; gender?: 'MALE' | 'FEMALE' | 'OTHER' } = {},
) {
  const res = await request.post(`${API_BASE}/patients`, {
    headers: authed(token),
    data: { name, whatsapp_number, ...opts },
  })
  if (!res.ok()) throw new Error(`createPatient failed: ${await res.text()}`)
  return res.json()
}

// A realistic clinic day (09:00-17:00) on every weekday, unlike
// setFullAvailability's deliberately unrealistic 00:00-23:45. The
// BookAppointmentPanel grid specs assert on the *shape* of a doctor's
// working day -- that it starts at its real first slot and shows every
// slot through its last -- which a 24-hour schedule can't express. Safe
// against the clock (unlike a narrow schedule used for a booking test)
// because those specs always navigate to Tomorrow, where the whole day
// is still ahead regardless of what time the run happens at.
export async function setClinicHours(
  request: APIRequestContext,
  token: string,
  doctorId: number,
  startTime = '09:00',
  endTime = '17:00',
) {
  for (let day = 1; day <= 7; day++) {
    const res = await request.post(`${API_BASE}/doctors/${doctorId}/schedule`, {
      headers: authed(token),
      data: { day_of_week: day, start_time: startTime, end_time: endTime },
    })
    if (!res.ok()) throw new Error(`setClinicHours failed: ${await res.text()}`)
  }
}

export async function addAllergy(
  request: APIRequestContext,
  token: string,
  patientId: number,
  allergen: string,
  opts: { reaction?: string; severity?: 'MILD' | 'MODERATE' | 'SEVERE' } = {},
) {
  const res = await request.post(`${API_BASE}/patients/${patientId}/allergies`, {
    headers: authed(token),
    data: { allergen, reaction: opts.reaction, severity: opts.severity },
  })
  if (!res.ok()) throw new Error(`addAllergy failed: ${await res.text()}`)
  return res.json()
}

// Books, confirms and checks in one appointment for `doctorId` +
// `patientId`, returning its id -- the minimum state every
// ConsultationWorkspace/orders/prescription test needs to reach a
// writable encounter (readOnly there is gated on appointment_status
// === 'CHECKED_IN', see ConsultationWorkspace.tsx). start_at defaults
// to "now" (admin create bypasses the patient scheduling window, same
// as the real admin Book Appointment page).
export async function bookAndCheckIn(
  request: APIRequestContext,
  token: string,
  opts: { doctorId: number; patientId: number; appointmentTypeId: number; startAt?: string },
) {
  // A few minutes in the future, not exactly "now" -- confirm_
  // appointment_service rejects a slot whose start_at has already
  // passed, and by the time this call reaches the server "now" always
  // has (round-trip latency alone is enough).
  const startAt = opts.startAt ?? new Date(Date.now() + 5 * 60_000).toISOString()
  const created = await request.post(`${API_BASE}/appointments`, {
    headers: authed(token),
    data: {
      doctor_id: opts.doctorId,
      patient_id: opts.patientId,
      appointment_type_id: opts.appointmentTypeId,
      start_at: startAt,
      booking_source: 'STAFF_ASSISTED',
    },
  })
  if (!created.ok()) throw new Error(`bookAndCheckIn create failed: ${await created.text()}`)
  const appointment = await created.json()

  const confirmed = await request.post(`${API_BASE}/appointments/${appointment.id}/confirm`, { headers: authed(token) })
  if (!confirmed.ok()) throw new Error(`bookAndCheckIn confirm failed: ${await confirmed.text()}`)

  const visited = await request.post(`${API_BASE}/appointments/${appointment.id}/visit`, { headers: authed(token) })
  if (!visited.ok()) throw new Error(`bookAndCheckIn visit failed: ${await visited.text()}`)

  return appointment.id as number
}

// Records a normal cash payment for a CHECKED_IN appointment, which is
// what actually assigns a queue token (token_number is set once
// payment succeeds or is waived, never at check-in itself -- see
// AdminAppointment.token_number's own comment in types.ts). Deliberately
// not waiveAppointmentPayment: that path is the ADMIN-only 3-day-
// revisit goodwill waiver (rejects with "Waiver requires a completed
// visit with this doctor in the last 3 days" otherwise) -- a normal
// paid visit, which every seeded appointment here is, pays instead.
export async function payAppointment(request: APIRequestContext, token: string, appointmentId: number) {
  const res = await request.post(`${API_BASE}/appointments/${appointmentId}/payment`, {
    headers: authed(token),
    data: { method: 'CASH', outcome: 'PAID' },
  })
  if (!res.ok()) throw new Error(`payAppointment failed: ${await res.text()}`)
  return res.json()
}

// Full reference-data setup one ConsultationWorkspace/AppointmentsPanel
// spec typically needs: a department, one doctor in it offering one
// appointment type all day, and a patient. Every name is uniq()'d so
// parallel/repeated runs against the same database never collide on
// departments.name/appointment_types.name's UNIQUE constraints.
export async function seedBasicScenario(request: APIRequestContext, token: string) {
  const department = await createDepartment(request, token, uniq('Dept'))
  const appointmentType = await createAppointmentType(request, token, uniq('Consultation'))
  const doctor = await createDoctor(request, token, uniq('Dr. Test'))
  await assignDoctorToDepartment(request, token, doctor.id, department.id)
  await assignAppointmentType(request, token, doctor.id, appointmentType.id, { duration_minutes: 20, consultation_fee: 500 })
  await setFullAvailability(request, token, doctor.id)
  const patient = await createPatient(request, token, uniq('Test Patient'), `+91${Math.floor(7000000000 + Math.random() * 999999999)}`)
  return { department, appointmentType, doctor, patient }
}
