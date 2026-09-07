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

// The compact profile fields every doctor-listing endpoint now carries
// (app/services/availability_engine.py's build_doctor_summary) --
// specialization/years_of_experience/qualifications/photo_url plus a
// derived education_location (the doctor's admin-chosen *featured*
// doctor_education entry, "Institution, City, Country" -- never the
// most recent one automatically, and null when nothing is featured).
// Mixed into Doctor and DoctorWithSlots below rather than duplicated,
// since both are actually the same summary shape at the wire level now.
export interface DoctorProfileSummary {
  specialization: string | null
  years_of_experience: number | null
  qualifications: string | null
  photo_url: string | null
  education_location: string | null
}

export interface Doctor extends DoctorProfileSummary {
  id: number
  name: string
  active: boolean
  created_at: string
  created_by: string | null
}

export interface DoctorEducationEntry {
  id: number
  doctor_id: number
  qualification: string
  institution: string
  city: string
  country: string
  completion_year: number
  is_primary: boolean
}

// GET /api/doctors/{id} -- the full profile + complete education
// history, fetched on demand for "View Profile". Deliberately never
// bundled into the compact DoctorProfileSummary above, which every
// listing/card uses instead.
export interface DoctorProfile extends DoctorProfileSummary {
  id: number
  name: string
  active: boolean
  created_at: string
  sub_specialization: string | null
  education: DoctorEducationEntry[]
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

// Date-First flow only: one entry of GET /web/availability/by-date's
// "doctors" list -- a doctor in the department offering the chosen
// appointment type, with their own real slots for the chosen date
// already attached (never present with an empty slots array).
export interface DoctorWithSlots extends DoctorProfileSummary {
  id: number
  name: string
  slots: Slot[]
}

export interface CalendarMonth {
  doctor_id: number
  appointment_type_id: number
  year: number
  month: number
  scheduling_window_start: string
  scheduling_window_end: string
  dates: Record<string, boolean>
}

export interface ScheduledAppointment {
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
// unlike ScheduledAppointment's fields above, which come straight off a
// create/reschedule INSERT ... RETURNING and are UTC-labeled; see
// format.ts and the WEB P3/P4 reports for why that distinction matters).
export interface MyAppointment {
  id: number
  doctor_id: number
  doctor_name: string
  doctor_specialization: string | null
  appointment_type_id: number
  appointment_type_name: string
  start_at: string
  end_at: string
  status: string
  // Assigned at check-in (status -> VISITED); null until then.
  token_number: number | null
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
  // Assigned at check-in (status -> VISITED); null until then.
  token_number: number | null
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
  pending_appointments: number
  confirmed_appointments: number
  rejected_appointments: number
  cancelled_appointments: number
  visited_appointments: number
  completed_appointments: number
  total_doctors: number
  total_patients: number
}

export interface DashboardTrendPoint {
  date: string
  count: number
}

export interface DashboardTrends {
  appointments: DashboardTrendPoint[]
  patients: DashboardTrendPoint[]
}

// GET /api/doctors/{id}/queue -- today's walk-in queue (migrations/0012).
export interface QueueEntry {
  appointment_id: number
  token_number: number
  visited_at: string
  patient_id: number
  patient_name: string
}

export interface DoctorQueue {
  doctor_id: number
  doctor_name: string
  date: string
  now_serving: QueueEntry | null
  waiting: QueueEntry[]
  completed: QueueEntry[]
}
