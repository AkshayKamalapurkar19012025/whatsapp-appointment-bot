export interface Patient {
  id: number
  name: string
  whatsapp_number: string
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
