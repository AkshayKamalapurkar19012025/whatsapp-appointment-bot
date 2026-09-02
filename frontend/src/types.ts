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
