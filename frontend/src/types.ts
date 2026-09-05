export interface Patient {
  id: number
  name: string
  whatsapp_number: string
  // Absent on the plain create-patient response (a brand new patient has
  // no appointments yet) -- only the list endpoint computes these.
  appointment_count?: number
  patient_type?: 'first-time' | 'recurring'
}

export interface Department {
  id: number
  name: string
  active: boolean
}

export interface Doctor {
  id: number
  name: string
  active: boolean
  created_at: string
  created_by: string | null
}

export interface AppointmentType {
  id: number
  name: string
  duration_minutes: number
  active: boolean
}

export interface Slot {
  start_at: string
  end_at: string
}

export interface CalendarMonth {
  doctor_id: number
  appointment_type_id: number
  year: number
  month: number
  booking_window_start: string
  booking_window_end: string
  dates: Record<string, boolean>
}

export interface BookedAppointment {
  id: number
  doctor_id: number
  patient_id: number
  appointment_type_id: number
  start_at: string
  end_at: string
  status: string
  duration_minutes: number
  appointment_type_name: string
}

// Note: start_at/end_at here are already correct in the doctor's own
// local time (app/services/appointment_services.py's
// list_patient_appointments_service converts them before returning --
// unlike BookedAppointment's fields above, which come straight off a
// create/reschedule INSERT ... RETURNING and are UTC-labeled; see
// format.ts and the WEB P3/P4 reports for why that distinction matters).
export interface MyAppointment {
  id: number
  doctor_id: number
  doctor_name: string
  appointment_type_id: number
  appointment_type_name: string
  start_at: string
  end_at: string
  status: string
}

export interface MyAppointmentsResponse {
  upcoming: MyAppointment[]
  history: MyAppointment[]
  cancelled: MyAppointment[]
}

// -- WEB P11: admin/staff types --------------------------------------------

export interface Staff {
  id: number
  username: string
  role: 'ADMIN' | 'STAFF'
}

export interface StaffAccount {
  id: number
  username: string
  role: 'ADMIN' | 'STAFF'
  active: boolean
}

// The plain appointment-type catalog shape (app/api/appointment_types.py) --
// no duration_minutes, since duration is set per doctor assignment (see
// AppointmentType above, which IS that per-assignment shape and already
// carries duration_minutes). Two interfaces, not one made-up-optional
// field, because a catalog entry and an assignment are different things
// with different fields, not the same thing missing data.
export interface AppointmentTypeSummary {
  id: number
  name: string
  active: boolean
}

export interface DoctorScheduleEntry {
  id: number
  day_of_week: number
  start_time: string
  end_time: string
  active: boolean
  start_date: string | null
  end_date: string | null
  // Null means this row applies regardless of department (migrations/0010).
  department_id: number | null
}

// start_at/end_at here are raw DB values (UTC-labeled on read-back, per
// app/api/doctor_blocks.py -- unlike appointments.py's admin listing,
// this endpoint was never given the WEB P9 doctor-local-time display
// fix, since it's outside that phase's own scope). The admin UI's block
// form only ever creates/shows blocks in Asia/Kolkata terms, since that's
// the only timezone any doctor in this system can currently have (no API
// exposes or sets doctors.timezone -- see the WEB P11 report).
export interface DoctorBlockEntry {
  id: number
  start_at: string
  end_at: string
  reason: string
  active: boolean
}

// app/api/appointments.py's admin listing (WEB P9) -- start_at/end_at
// ARE already doctor-local here, unlike DoctorBlockEntry above.
export interface AdminAppointment {
  id: number
  doctor_id: number
  doctor_name: string
  patient_id: number
  patient_name: string
  whatsapp_number: string
  appointment_type_id: number
  appointment_type_name: string
  start_at: string
  end_at: string
  status: string
}

export interface AdminAppointmentActionResult {
  id: number
  doctor_id: number
  patient_id: number
  appointment_type_id: number
  start_at: string
  end_at: string
  status: string
  duration_minutes: number
  appointment_type_name: string
}

export interface DashboardStats {
  today_appointments: number
  upcoming_appointments: number
  total_appointments: number
  cancelled_appointments: number
  total_doctors: number
  total_patients: number
}
