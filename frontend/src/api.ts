import type {
  AdminAppointment,
  AdminAppointmentActionResult,
  AppointmentType,
  AppointmentTypeSummary,
  BookedAppointment,
  CalendarMonth,
  Department,
  Doctor,
  DoctorBlockEntry,
  DoctorScheduleEntry,
  MyAppointmentsResponse,
  Patient,
  Staff,
  StaffAccount,
} from './types'

// Bearer token, per the WEB P2 decision (Authorization header, not a
// cookie). Kept in localStorage for simplicity in this phase -- disciplined
// output-encoding to limit XSS exposure is a frontend-hardening concern
// for later (flagged, not silently ignored; see the WEB P3 report).
const TOKEN_KEY = 'patient_session_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

// A separate token/storage key for staff sessions (WEB P11) -- never the
// same key as the patient token above. Patient and staff are distinct
// subject types with their own session tables/tokens on the backend
// (app/services/staff_auth.py's module docstring); keeping them in
// separate localStorage keys means a staff session and a patient session
// can coexist in the same browser (e.g. testing both UIs) without either
// clobbering the other, and a bug can never accidentally send a staff
// token to a patient endpoint or vice versa.
const STAFF_TOKEN_KEY = 'staff_session_token'

export function getStaffToken(): string | null {
  return localStorage.getItem(STAFF_TOKEN_KEY)
}

export function setStaffToken(token: string): void {
  localStorage.setItem(STAFF_TOKEN_KEY, token)
}

export function clearStaffToken(): void {
  localStorage.removeItem(STAFF_TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean | 'staff' } = {},
): Promise<T> {
  const headers: Record<string, string> = {}
  let body: string | undefined

  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }

  if (options.auth === 'staff') {
    const token = getStaffToken()
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
  } else if (options.auth) {
    const token = getToken()
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
  }

  const response = await fetch(`/api${path}`, {
    method: options.method ?? 'GET',
    headers,
    body,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const errorBody = await response.json()
      detail = errorBody.detail ?? detail
    } catch {
      // Non-JSON error body -- fall back to statusText.
    }
    throw new ApiError(response.status, detail)
  }

  return response.json() as Promise<T>
}

// -- Patient auth --------------------------------------------------------

export function requestOtp(whatsappNumber: string): Promise<{ message: string }> {
  return request('/auth/patient/otp/request', {
    method: 'POST',
    body: { whatsapp_number: whatsappNumber },
  })
}

export function verifyOtp(
  whatsappNumber: string,
  otp: string,
  name?: string,
): Promise<
  | { registration_required: true; whatsapp_number: string }
  | { patient: Patient; session_token: string; is_new_patient: boolean }
> {
  return request('/auth/patient/otp/verify', {
    method: 'POST',
    body: { whatsapp_number: whatsappNumber, otp, name },
  })
}

export function getMe(): Promise<Patient> {
  return request('/auth/patient/me', { auth: true })
}

export function logout(): Promise<{ message: string }> {
  return request('/auth/patient/logout', { method: 'POST', auth: true })
}

// -- Reference data (departments/doctors/appointment types) --------------

export function listDepartments(): Promise<Department[]> {
  return request('/departments')
}

export function listDoctorsInDepartment(departmentId: number): Promise<Doctor[]> {
  return request(`/departments/${departmentId}/doctors`)
}

export function listAppointmentTypesForDoctor(doctorId: number): Promise<AppointmentType[]> {
  return request(`/doctors/${doctorId}/appointment-types`)
}

// -- Availability / booking -----------------------------------------------

export function getCalendarMonth(
  doctorId: number,
  appointmentTypeId: number,
  year: number,
  month: number,
): Promise<CalendarMonth> {
  const params = new URLSearchParams({
    doctor_id: String(doctorId),
    appointment_type_id: String(appointmentTypeId),
    year: String(year),
    month: String(month),
  })
  return request(`/web/calendar?${params.toString()}`)
}

export function getSlotsForDate(
  doctorId: number,
  appointmentTypeId: number,
  isoDate: string,
): Promise<{ slots: { start_at: string; end_at: string }[]; duration_minutes: number }> {
  return request('/availability', {
    method: 'POST',
    body: {
      doctor_id: doctorId,
      appointment_type_id: appointmentTypeId,
      date: isoDate,
    },
  })
}

export function createWebAppointment(
  doctorId: number,
  appointmentTypeId: number,
  startAt: string,
): Promise<BookedAppointment> {
  return request('/web/appointments', {
    method: 'POST',
    auth: true,
    body: {
      doctor_id: doctorId,
      appointment_type_id: appointmentTypeId,
      start_at: startAt,
    },
  })
}

// -- My Appointments (WEB P4) ---------------------------------------------

export function getMyAppointments(): Promise<MyAppointmentsResponse> {
  return request('/web/appointments/me', { auth: true })
}

export function cancelWebAppointment(
  appointmentId: number,
): Promise<{ id: number; status: string; message: string }> {
  return request(`/web/appointments/${appointmentId}`, {
    method: 'DELETE',
    auth: true,
  })
}

export function rescheduleWebAppointment(
  appointmentId: number,
  newStartAt: string,
): Promise<BookedAppointment> {
  return request(`/web/appointments/${appointmentId}/reschedule`, {
    method: 'POST',
    auth: true,
    body: { new_start_at: newStartAt },
  })
}

// -- WEB P11: staff auth ---------------------------------------------------

export function staffLogin(
  username: string,
  password: string,
): Promise<{ staff: Staff; session_token: string }> {
  return request('/auth/staff/login', {
    method: 'POST',
    body: { username, password },
  })
}

export function getStaffMe(): Promise<Staff> {
  return request('/auth/staff/me', { auth: 'staff' })
}

export function staffLogout(): Promise<{ message: string }> {
  return request('/auth/staff/logout', { method: 'POST', auth: 'staff' })
}

// -- WEB P11: staff accounts (ADMIN only) -----------------------------------

export function listStaffAccounts(): Promise<StaffAccount[]> {
  return request('/auth/staff/accounts', { auth: 'staff' })
}

export function createStaffAccount(
  username: string,
  password: string,
  role: 'ADMIN' | 'STAFF',
): Promise<StaffAccount> {
  return request('/auth/staff/accounts', {
    method: 'POST',
    auth: 'staff',
    body: { username, password, role },
  })
}

export function setStaffAccountActive(
  staffId: number,
  active: boolean,
): Promise<StaffAccount> {
  return request(`/auth/staff/accounts/${staffId}/active`, {
    method: 'PATCH',
    auth: 'staff',
    body: { active },
  })
}

// -- WEB P11: departments/doctors/appointment-types admin writes -----------
// (the GETs -- listDepartments, listDoctorsInDepartment,
// listAppointmentTypesForDoctor -- are already above, shared with the
// patient booking flow, and stay public/unauthenticated per WEB P6.)

export function createDepartment(name: string): Promise<Department> {
  return request('/departments', { method: 'POST', auth: 'staff', body: { name } })
}

export function listAllDoctors(): Promise<Doctor[]> {
  return request('/doctors')
}

export function createDoctor(name: string): Promise<Doctor> {
  return request('/doctors', { method: 'POST', auth: 'staff', body: { name } })
}

export function getDoctorDepartments(doctorId: number): Promise<Department[]> {
  return request(`/doctors/${doctorId}/departments`)
}

export function assignDoctorToDepartment(
  doctorId: number,
  departmentId: number,
): Promise<{ doctor_id: number; department_id: number; department_name: string }> {
  return request(`/doctors/${doctorId}/departments/${departmentId}`, {
    method: 'POST',
    auth: 'staff',
  })
}

export function removeDoctorFromDepartment(
  doctorId: number,
  departmentId: number,
): Promise<{ doctor_id: number; department_id: number; message: string }> {
  return request(`/doctors/${doctorId}/departments/${departmentId}`, {
    method: 'DELETE',
    auth: 'staff',
  })
}

export function listAppointmentTypeCatalog(): Promise<AppointmentTypeSummary[]> {
  return request('/appointment-types')
}

export function createAppointmentType(name: string): Promise<AppointmentTypeSummary> {
  return request('/appointment-types', { method: 'POST', auth: 'staff', body: { name } })
}

export function assignAppointmentTypeToDoctor(
  doctorId: number,
  appointmentTypeId: number,
  durationMinutes: number,
): Promise<AppointmentType & { appointment_type_name: string }> {
  return request(`/doctors/${doctorId}/appointment-types/${appointmentTypeId}`, {
    method: 'POST',
    auth: 'staff',
    body: { duration_minutes: durationMinutes },
  })
}

export function removeAppointmentTypeFromDoctor(
  doctorId: number,
  appointmentTypeId: number,
): Promise<{ message: string }> {
  return request(`/doctors/${doctorId}/appointment-types/${appointmentTypeId}`, {
    method: 'DELETE',
    auth: 'staff',
  })
}

// -- WEB P11: doctor recurring schedule (ADMIN only) ------------------------

export function getDoctorScheduleAdmin(doctorId: number): Promise<DoctorScheduleEntry[]> {
  return request(`/doctors/${doctorId}/schedule`)
}

export function createDoctorSchedule(
  doctorId: number,
  entry: {
    day_of_week: number
    start_time: string
    end_time: string
    start_date?: string | null
    end_date?: string | null
  },
): Promise<DoctorScheduleEntry> {
  return request(`/doctors/${doctorId}/schedule`, {
    method: 'POST',
    auth: 'staff',
    body: entry,
  })
}

export function deleteDoctorSchedule(
  doctorId: number,
  scheduleId: number,
): Promise<{ message: string }> {
  return request(`/doctors/${doctorId}/schedule/${scheduleId}`, {
    method: 'DELETE',
    auth: 'staff',
  })
}

// -- WEB P11: doctor one-off blocks (ADMIN or STAFF) ------------------------
// See types.ts's DoctorBlockEntry docstring: block times are entered/shown
// in Asia/Kolkata terms, the only timezone any doctor in this system can
// currently have (no API exposes/sets doctors.timezone).

export function getDoctorBlocks(doctorId: number): Promise<DoctorBlockEntry[]> {
  return request(`/doctors/${doctorId}/blocks`)
}

export function createDoctorBlock(
  doctorId: number,
  startAt: string,
  endAt: string,
  reason: string,
): Promise<DoctorBlockEntry> {
  return request(`/doctors/${doctorId}/blocks`, {
    method: 'POST',
    auth: 'staff',
    body: { start_at: startAt, end_at: endAt, reason },
  })
}

export function deleteDoctorBlock(
  doctorId: number,
  blockId: number,
): Promise<{ message: string }> {
  return request(`/doctors/${doctorId}/blocks/${blockId}`, {
    method: 'DELETE',
    auth: 'staff',
  })
}

// -- WEB P11: patients (ADMIN or STAFF) -------------------------------------

export function listPatients(): Promise<Patient[]> {
  return request('/patients', { auth: 'staff' })
}

export function createPatientAdmin(name: string, whatsappNumber: string): Promise<Patient> {
  return request('/patients', {
    method: 'POST',
    auth: 'staff',
    body: { name, whatsapp_number: whatsappNumber },
  })
}

// -- WEB P11: admin appointment management (ADMIN or STAFF, WEB P9) --------

export function listAdminAppointments(filters: {
  doctor_id?: number
  patient_id?: number
  status?: string
}): Promise<AdminAppointment[]> {
  const params = new URLSearchParams()
  if (filters.doctor_id !== undefined) params.set('doctor_id', String(filters.doctor_id))
  if (filters.patient_id !== undefined) params.set('patient_id', String(filters.patient_id))
  if (filters.status) params.set('status', filters.status)
  const query = params.toString()
  return request(`/appointments${query ? `?${query}` : ''}`, { auth: 'staff' })
}

export function createAdminAppointment(
  doctorId: number,
  patientId: number,
  appointmentTypeId: number,
  startAt: string,
): Promise<AdminAppointmentActionResult> {
  return request('/appointments', {
    method: 'POST',
    auth: 'staff',
    body: {
      doctor_id: doctorId,
      patient_id: patientId,
      appointment_type_id: appointmentTypeId,
      start_at: startAt,
    },
  })
}

export function cancelAdminAppointment(
  appointmentId: number,
): Promise<{ id: number; status: string; message: string }> {
  return request(`/appointments/${appointmentId}`, { method: 'DELETE', auth: 'staff' })
}

export function rescheduleAdminAppointment(
  appointmentId: number,
  newStartAt: string,
): Promise<AdminAppointmentActionResult> {
  return request(`/appointments/${appointmentId}/reschedule`, {
    method: 'POST',
    auth: 'staff',
    body: { new_start_at: newStartAt },
  })
}
