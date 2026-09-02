import type {
  AppointmentType,
  BookedAppointment,
  CalendarMonth,
  Department,
  Doctor,
  Patient,
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

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {}
  let body: string | undefined

  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }

  if (options.auth) {
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
