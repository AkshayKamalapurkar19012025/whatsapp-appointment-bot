import type {
  AdminAppointment,
  AdminAppointmentActionResult,
  ArrivalActionResult,
  MarkArrivedResult,
  AppointmentType,
  AppointmentTypeAdminRow,
  AppointmentTypeDetail,
  AppointmentTypeSummary,
  ScheduledAppointment,
  BookingSource,
  CalendarMonth,
  DashboardStats,
  DashboardTrends,
  Department,
  Doctor,
  DoctorBlockEntry,
  DoctorDepartmentAssignment,
  DoctorEducationEntry,
  DoctorProfile,
  DoctorQueue,
  DoctorScheduleEntry,
  DoctorWithSlots,
  MyAppointmentsResponse,
  Patient,
  PatientGender,
  PaymentActionResult,
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

// FastAPI's own automatic request-validation errors (a bad type/missing
// field caught by Pydantic before a route body even runs) return `detail`
// as a list of {loc, msg, type} objects, not the plain string every
// hand-written `HTTPException(detail="...")` in this app's routes uses --
// callers here have only ever seen the plain-string shape, so `detail`
// landing as an array/object was silently becoming "[object Object]"
// once coerced into an Error's message. Reduces either shape down to one
// readable string; unrecognized shapes fall back to the caller's default
// rather than ever surfacing an unstringified object.
function stringifyErrorDetail(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail.map((entry) => {
      if (typeof entry === 'string') return entry
      if (entry && typeof entry === 'object' && 'msg' in entry) {
        const e = entry as { loc?: unknown[]; msg?: unknown }
        const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : null
        return field ? `${field}: ${e.msg}` : String(e.msg)
      }
      return null
    })
    const joined = messages.filter((m): m is string => m !== null).join('; ')
    return joined || undefined
  }
  return undefined
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
      detail = stringifyErrorDetail(errorBody.detail) ?? detail
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

// -- App-wide display config ---------------------------------------------

export function getAppConfig(): Promise<{ default_timezone: string }> {
  return request('/app-config')
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

// Date-First flow only: appointment types offered by ANY doctor in the
// department, needed before a doctor is chosen -- unlike
// listAppointmentTypesForDoctor above (Doctor-First, doctor already
// known). No duration_minutes here: duration is per doctor+type, only
// meaningful once a specific doctor is known (same as Doctor-First).
export function listAppointmentTypesForDepartment(
  departmentId: number,
): Promise<AppointmentTypeSummary[]> {
  return request(`/departments/${departmentId}/appointment-types`)
}

// -- Availability / scheduling -----------------------------------------------

export function getCalendarMonth(
  doctorId: number,
  appointmentTypeId: number,
  year: number,
  month: number,
  // Narrows results to this department's schedule rows (plus any
  // department-agnostic ones) -- see migrations/0010. Omit when there's
  // no department in scope (e.g. rescheduling), which sees every row.
  departmentId?: number,
): Promise<CalendarMonth> {
  const params = new URLSearchParams({
    doctor_id: String(doctorId),
    appointment_type_id: String(appointmentTypeId),
    year: String(year),
    month: String(month),
  })
  if (departmentId !== undefined) params.set('department_id', String(departmentId))
  return request(`/web/calendar?${params.toString()}`)
}

export function getSlotsForDate(
  doctorId: number,
  appointmentTypeId: number,
  isoDate: string,
  departmentId?: number,
): Promise<{
  slots: { start_at: string; end_at: string }[]
  duration_minutes: number
  total_slots: number
}> {
  return request('/availability', {
    method: 'POST',
    body: {
      doctor_id: doctorId,
      appointment_type_id: appointmentTypeId,
      date: isoDate,
      ...(departmentId !== undefined ? { department_id: departmentId } : {}),
    },
  })
}

// Date-First's aggregate month calendar: same response shape as
// getCalendarMonth above (a per-day boolean map), but "available" means
// ANY doctor in the department offering this appointment type has a
// real slot -- no doctor_id is known yet at this step.
export function getDepartmentCalendarMonth(
  departmentId: number,
  appointmentTypeId: number,
  year: number,
  month: number,
): Promise<CalendarMonth> {
  const params = new URLSearchParams({
    department_id: String(departmentId),
    appointment_type_id: String(appointmentTypeId),
    year: String(year),
    month: String(month),
  })
  return request(`/web/calendar/department?${params.toString()}`)
}

// Date-First's per-date doctor list: every doctor in the department
// offering this appointment type with at least one real slot on this
// date, each already carrying their own slots -- a doctor with zero
// valid slots is never included.
export function getDoctorsForDate(
  departmentId: number,
  appointmentTypeId: number,
  isoDate: string,
): Promise<{ doctors: DoctorWithSlots[] }> {
  const params = new URLSearchParams({
    department_id: String(departmentId),
    appointment_type_id: String(appointmentTypeId),
    selected_date: isoDate,
  })
  return request(`/web/availability/by-date?${params.toString()}`)
}

export function createWebAppointment(
  doctorId: number,
  appointmentTypeId: number,
  startAt: string,
): Promise<ScheduledAppointment> {
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
): Promise<ScheduledAppointment> {
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

// -- Dashboard ---------------------------------------------------------

export function getDashboardStats(): Promise<DashboardStats> {
  return request('/dashboard/stats', { auth: 'staff' })
}

export function getDashboardTrends(days = 14): Promise<DashboardTrends> {
  return request(`/dashboard/trends?days=${days}`, { auth: 'staff' })
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
// patient scheduling flow, and stay public/unauthenticated per WEB P6.)

export function createDepartment(name: string): Promise<Department> {
  return request('/departments', { method: 'POST', auth: 'staff', body: { name } })
}

export function updateDepartment(departmentId: number, name: string): Promise<Department> {
  return request(`/departments/${departmentId}`, { method: 'PUT', auth: 'staff', body: { name } })
}

export function deleteDepartment(departmentId: number): Promise<{ id: number; message: string }> {
  return request(`/departments/${departmentId}`, { method: 'DELETE', auth: 'staff' })
}

export function listAllDoctors(): Promise<Doctor[]> {
  return request('/doctors')
}

// The body shape for both POST /doctors (create) and PUT /doctors/{id}
// (update) -- app/api/doctors.py's DoctorCreate is reused unchanged for
// both, so this mirrors that on the frontend too, rather than two
// near-identical types drifting apart.
export interface DoctorProfileInput {
  name: string
  specialization: string
  sub_specialization?: string | null
  qualifications?: string | null
  years_of_experience?: number | null
}

// Its response also carries created_by (this admin's own username) and
// an always-null education_location (a brand-new doctor can't have a
// featured education entry yet) -- together a full Doctor shape, unlike
// updateDoctor/getDoctorProfile below which return the plainer
// DoctorProfile (no created_by, since editing/viewing doesn't need it).
export function createDoctor(payload: DoctorProfileInput): Promise<Doctor> {
  return request('/doctors', { method: 'POST', auth: 'staff', body: payload })
}

export function updateDoctor(doctorId: number, payload: DoctorProfileInput): Promise<DoctorProfile> {
  return request(`/doctors/${doctorId}`, { method: 'PUT', auth: 'staff', body: payload })
}

// Unauthenticated -- also used by the patient-facing "View Profile"
// experience mid-scheduling (SchedulingFlow.tsx), not just the admin panel.
export function getDoctorProfile(doctorId: number): Promise<DoctorProfile> {
  return request(`/doctors/${doctorId}`)
}

// Schedule tab's "Slot settings" panel (migrations/0022_doctor_slot_
// settings.sql) -- its own small PATCH rather than folded into
// updateDoctor's PUT, which requires resending every scalar field on
// the doctor.
export function updateDoctorSlotSettings(
  doctorId: number,
  payload: { default_duration_minutes: number; buffer_minutes: number },
): Promise<{ id: number; default_duration_minutes: number; buffer_minutes: number }> {
  return request(`/doctors/${doctorId}/slot-settings`, { method: 'PATCH', auth: 'staff', body: payload })
}

// Doctor workspace header's "..." menu -- deactivate/reactivate. Every
// other doctor-scoped endpoint already treats active=FALSE as "doesn't
// exist", so this is what actually removes a doctor from listings,
// booking, and availability without deleting their history.
export function setDoctorActive(doctorId: number, active: boolean): Promise<{ id: number; active: boolean }> {
  return request(`/doctors/${doctorId}/active`, { method: 'PATCH', auth: 'staff', body: { active } })
}

export function getDoctorDepartments(doctorId: number): Promise<DoctorDepartmentAssignment[]> {
  return request(`/doctors/${doctorId}/departments`)
}

// -- WEB P11-style: doctor Education & Training entries (ADMIN only) -------

export interface DoctorEducationInput {
  qualification: string
  institution: string
  city: string
  country: string
  completion_year: number
}

export function addDoctorEducation(
  doctorId: number,
  entry: DoctorEducationInput,
): Promise<DoctorEducationEntry> {
  return request(`/doctors/${doctorId}/education`, {
    method: 'POST',
    auth: 'staff',
    body: entry,
  })
}

export function removeDoctorEducation(
  doctorId: number,
  educationId: number,
): Promise<{ id: number; doctor_id: number; message: string }> {
  return request(`/doctors/${doctorId}/education/${educationId}`, {
    method: 'DELETE',
    auth: 'staff',
  })
}

// Marks this entry as the one compact scheduling cards show as
// "education_location" -- unmarking whatever entry (if any) was
// previously featured, per "only one education entry per doctor can be
// featured" (see migrations/0014_doctor_profile.sql's partial unique
// index).
export function featureDoctorEducation(
  doctorId: number,
  educationId: number,
): Promise<DoctorEducationEntry> {
  return request(`/doctors/${doctorId}/education/${educationId}/feature`, {
    method: 'POST',
    auth: 'staff',
  })
}

export function unfeatureDoctorEducation(
  doctorId: number,
  educationId: number,
): Promise<DoctorEducationEntry> {
  return request(`/doctors/${doctorId}/education/${educationId}/feature`, {
    method: 'DELETE',
    auth: 'staff',
  })
}

// -- Doctor profile photo (ADMIN only) --------------------------------------
// Multipart upload, unlike every other admin write above -- bypasses the
// shared request() helper (which always JSON-encodes) and talks to
// fetch directly, since a File body needs FormData with no explicit
// Content-Type (the browser sets its own multipart boundary).

export async function uploadDoctorPhoto(
  doctorId: number,
  file: File,
): Promise<{ doctor_id: number; photo_url: string }> {
  const formData = new FormData()
  formData.append('file', file)

  const headers: Record<string, string> = {}
  const token = getStaffToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`/api/doctors/${doctorId}/photo`, {
    method: 'POST',
    headers,
    body: formData,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const errorBody = await response.json()
      detail = stringifyErrorDetail(errorBody.detail) ?? detail
    } catch {
      // Non-JSON error body -- fall back to statusText.
    }
    throw new ApiError(response.status, detail)
  }

  return response.json()
}

export function removeDoctorPhoto(
  doctorId: number,
): Promise<{ doctor_id: number; photo_url: null }> {
  return request(`/doctors/${doctorId}/photo`, { method: 'DELETE', auth: 'staff' })
}

export function getDoctorQueue(doctorId: number): Promise<DoctorQueue> {
  return request(`/doctors/${doctorId}/queue`, { auth: 'staff' })
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

export function updateAppointmentType(
  appointmentTypeId: number,
  name: string,
): Promise<AppointmentTypeSummary> {
  return request(`/appointment-types/${appointmentTypeId}`, {
    method: 'PUT',
    auth: 'staff',
    body: { name },
  })
}

export function deleteAppointmentType(appointmentTypeId: number): Promise<{ id: number; message: string }> {
  return request(`/appointment-types/${appointmentTypeId}`, { method: 'DELETE', auth: 'staff' })
}

// The redesigned Appointment Types admin page's own listing -- unlike
// listAppointmentTypeCatalog above (public, active-only, and still
// used as-is by AppointmentsPanel's filter and DoctorWorkspace's
// "assign a new type" list), this one is staff-only and includes
// inactive types plus a real per-type doctor count.
export function listAppointmentTypesAdmin(): Promise<AppointmentTypeAdminRow[]> {
  return request('/appointment-types/admin', { auth: 'staff' })
}

// The View drawer -- one call for name/status/doctor count plus the
// doctor/duration/fee table, all read from existing data.
export function getAppointmentTypeDetail(appointmentTypeId: number): Promise<AppointmentTypeDetail> {
  return request(`/appointment-types/${appointmentTypeId}`, { auth: 'staff' })
}

// Deactivate/reactivate -- mirrors setDoctorActive exactly.
export function setAppointmentTypeActive(
  appointmentTypeId: number,
  active: boolean,
): Promise<{ id: number; active: boolean }> {
  return request(`/appointment-types/${appointmentTypeId}/active`, {
    method: 'PATCH',
    auth: 'staff',
    body: { active },
  })
}

export function assignAppointmentTypeToDoctor(
  doctorId: number,
  appointmentTypeId: number,
  durationMinutes: number,
  consultationFee: number = 0,
): Promise<AppointmentType & { appointment_type_name: string }> {
  return request(`/doctors/${doctorId}/appointment-types/${appointmentTypeId}`, {
    method: 'POST',
    auth: 'staff',
    body: { duration_minutes: durationMinutes, consultation_fee: consultationFee },
  })
}

// Change duration/fee for an already-assigned appointment type -- the
// backend has always had this PUT (app/api/doctor_appointment_types.py's
// update_appointment_type_duration), the frontend just never called it;
// assignAppointmentTypeToDoctor's POST 409s on an active assignment.
export function updateDoctorAppointmentType(
  doctorId: number,
  appointmentTypeId: number,
  durationMinutes: number,
  consultationFee: number,
): Promise<AppointmentType & { appointment_type_name: string }> {
  return request(`/doctors/${doctorId}/appointment-types/${appointmentTypeId}`, {
    method: 'PUT',
    auth: 'staff',
    body: { duration_minutes: durationMinutes, consultation_fee: consultationFee },
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
    department_id?: number | null
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

export function updateDoctorBlock(
  doctorId: number,
  blockId: number,
  startAt: string,
  endAt: string,
  reason: string,
): Promise<DoctorBlockEntry> {
  return request(`/doctors/${doctorId}/blocks/${blockId}`, {
    method: 'PUT',
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

// GET /patients/search -- the OPD find/register step's backend lookup
// (name/phone/UHID substring, optional exact DOB match), unlike
// listPatients above (the whole registry, filtered client-side by
// PatientsPanel's directory table). At least one of query/dob is
// required server-side; returns [] rather than 404 when nothing
// matches.
export function searchPatientsAdmin(query: string, dob?: string | null): Promise<Patient[]> {
  const params = new URLSearchParams()
  if (query.trim()) params.set('q', query.trim())
  if (dob) params.set('dob', dob)
  return request(`/patients/search?${params.toString()}`, { auth: 'staff' })
}

export function createPatientAdmin(
  name: string,
  whatsappNumber: string,
  dateOfBirth?: string | null,
  gender?: PatientGender | null,
): Promise<Patient> {
  return request('/patients', {
    method: 'POST',
    auth: 'staff',
    body: {
      name,
      whatsapp_number: whatsappNumber,
      date_of_birth: dateOfBirth || null,
      gender: gender || null,
    },
  })
}

// PATCH /patients/{id} -- staff correcting/completing a patient's
// details (front-desk "verify details" step, or the Patients page's
// Edit action). Same required/optional shape as createPatientAdmin.
export function updatePatientAdmin(
  patientId: number,
  name: string,
  whatsappNumber: string,
  dateOfBirth?: string | null,
  gender?: PatientGender | null,
): Promise<Patient> {
  return request(`/patients/${patientId}`, {
    method: 'PATCH',
    auth: 'staff',
    body: {
      name,
      whatsapp_number: whatsappNumber,
      date_of_birth: dateOfBirth || null,
      gender: gender || null,
    },
  })
}

// -- WEB P11: admin appointment management (ADMIN or STAFF, WEB P9) --------

export function listAdminAppointments(filters: {
  doctor_id?: number
  patient_id?: number
  status?: string
  appointment_type_id?: number
  date_from?: string
  date_to?: string
}): Promise<AdminAppointment[]> {
  const params = new URLSearchParams()
  if (filters.doctor_id !== undefined) params.set('doctor_id', String(filters.doctor_id))
  if (filters.patient_id !== undefined) params.set('patient_id', String(filters.patient_id))
  if (filters.status) params.set('status', filters.status)
  if (filters.appointment_type_id !== undefined) {
    params.set('appointment_type_id', String(filters.appointment_type_id))
  }
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  const query = params.toString()
  return request(`/appointments${query ? `?${query}` : ''}`, { auth: 'staff' })
}

export function createAdminAppointment(
  doctorId: number,
  patientId: number,
  appointmentTypeId: number,
  startAt: string,
  bookingSource?: BookingSource | null,
): Promise<AdminAppointmentActionResult> {
  return request('/appointments', {
    method: 'POST',
    auth: 'staff',
    body: {
      doctor_id: doctorId,
      patient_id: patientId,
      appointment_type_id: appointmentTypeId,
      start_at: startAt,
      booking_source: bookingSource || null,
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

// Lifecycle transitions (migrations/0011_appointment_lifecycle_
// statuses.sql, extended by migrations/0015): PENDING -> CONFIRMED ->
// CHECKED_IN -> COMPLETED, with PENDING -> REJECTED, PENDING/CONFIRMED
// -> CANCELLED (cancelAdminAppointment above), or CONFIRMED -> NO_SHOW
// (noShowAdminAppointment below, manual front-desk action only) as
// exits.

export function confirmAdminAppointment(
  appointmentId: number,
): Promise<{ id: number; status: string }> {
  return request(`/appointments/${appointmentId}/confirm`, { method: 'POST', auth: 'staff' })
}

export function rejectAdminAppointment(
  appointmentId: number,
): Promise<{ id: number; status: string }> {
  return request(`/appointments/${appointmentId}/reject`, { method: 'POST', auth: 'staff' })
}

export function visitAdminAppointment(
  appointmentId: number,
  // token_number is always null here as of patient arrival workflow
  // Phase 4 -- check-in no longer assigns one itself (see
  // record_appointment_payment/waiveAppointmentPayment below, the
  // actual queue-entry trigger).
): Promise<{ id: number; status: string; token_number: null; visited_at: string }> {
  return request(`/appointments/${appointmentId}/visit`, { method: 'POST', auth: 'staff' })
}

// Records a physical arrival -- usable any time before start_at (most
// commonly an early arrival), never sets status=CHECKED_IN and never
// generates a queue token. See ArrivalActionResult/mark_arrived_service.
export function markArrivedAdmin(appointmentId: number): Promise<MarkArrivedResult> {
  return request(`/appointments/${appointmentId}/arrive`, { method: 'POST', auth: 'staff' })
}

// The walk-in "Confirm & Check In" combined action -- composes
// confirm/visit/arrive server-side (confirm_and_check_in_service).
// result.arrival_kind says whether it actually reached CHECKED_IN or
// fell back to "arrived early" because start_at was still in the future.
export function confirmAndCheckInAdmin(appointmentId: number): Promise<ArrivalActionResult> {
  return request(`/appointments/${appointmentId}/confirm-and-checkin`, { method: 'POST', auth: 'staff' })
}

// -- Patient arrival workflow Phases 3-4: consultation charge + payment ----

export function getAppointmentCharge(
  appointmentId: number,
): Promise<{ appointment_id: number; consultation_fee: number }> {
  return request(`/appointments/${appointmentId}/charge`, { auth: 'staff' })
}

export function recordAppointmentPayment(
  appointmentId: number,
  method: 'CASH' | 'UPI' | 'CARD' | 'OTHER',
  outcome: 'PAID' | 'FAILED',
): Promise<PaymentActionResult> {
  return request(`/appointments/${appointmentId}/payment`, {
    method: 'POST',
    auth: 'staff',
    body: { method, outcome },
  })
}

export function waiveAppointmentPayment(
  appointmentId: number,
  reason: string,
): Promise<PaymentActionResult> {
  return request(`/appointments/${appointmentId}/waive-payment`, {
    method: 'POST',
    auth: 'staff',
    body: { reason },
  })
}

// POST .../settle-free-visit -- the OPD flow's "Payment Required? No"
// branch (settle_free_visit_service): auto-settles a CHECKED_IN visit
// whose configured consultation fee is 0, generating its queue token
// without a manual Collect Payment/Waive Charge step. Distinct from
// waiveAppointmentPayment above (ADMIN-only, 3-day-revisit policy for
// a REAL fee) -- any staff role can call this, and the backend itself
// refuses it if the fee turns out to be nonzero.
export function settleFreeVisitAdmin(appointmentId: number): Promise<PaymentActionResult> {
  return request(`/appointments/${appointmentId}/settle-free-visit`, { method: 'POST', auth: 'staff' })
}

export function completeAdminAppointment(
  appointmentId: number,
): Promise<{ id: number; status: string }> {
  return request(`/appointments/${appointmentId}/complete`, { method: 'POST', auth: 'staff' })
}

export function noShowAdminAppointment(
  appointmentId: number,
): Promise<{ id: number; status: string }> {
  return request(`/appointments/${appointmentId}/no-show`, { method: 'POST', auth: 'staff' })
}

// Staff-only month availability for the admin date picker -- unlike
// getCalendarMonth (patient-facing, scheduling-window-limited), this never
// rejects or blanks out a far-future month, matching how staff/admin
// schedulings are exempt from the patient scheduling window everywhere else.
export function getAdminCalendarMonth(
  doctorId: number,
  appointmentTypeId: number,
  year: number,
  month: number,
): Promise<{ dates: Record<string, boolean> }> {
  const params = new URLSearchParams({
    doctor_id: String(doctorId),
    appointment_type_id: String(appointmentTypeId),
    year: String(year),
    month: String(month),
  })
  return request(`/appointments/calendar?${params.toString()}`, { auth: 'staff' })
}
