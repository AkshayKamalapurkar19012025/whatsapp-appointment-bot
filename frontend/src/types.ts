export type PatientGender = 'MALE' | 'FEMALE' | 'OTHER'

export interface Patient {
  id: number
  name: string
  whatsapp_number: string
  // Absent on the plain create-patient response (a brand new patient has
  // no appointments yet) -- only the list endpoint computes these.
  appointment_count?: number
  // Kept for any existing reader, but the OPD Patients page no longer
  // treats this as the patient's primary/permanent attribute -- a
  // patient isn't permanently "first-time". Prefer appointment_count/
  // last_visit_at, which the page actually displays.
  patient_type?: 'first-time' | 'recurring'
  // Most recent non-cancelled/rejected appointment's start_at, or null
  // if the patient has none yet. Same list-endpoint-only availability
  // as appointment_count above.
  last_visit_at?: string | null
  // Both optional everywhere -- registration never requires either
  // (migrations/0023). null on every patient created before this
  // existed, or who simply hasn't had them added yet.
  date_of_birth: string | null
  gender: PatientGender | null
  // "HOS-0000123" (migrations/0024) -- the patient's permanent
  // hospital identifier, derived from id and never mutated. This, not
  // whatsapp_number, is what the OPD find/register flow treats as a
  // patient's real identity; whatsapp_number stays a separate
  // search/contact attribute. Always present (a stored generated
  // column), unlike the appointment_count/last_visit_at fields above.
  uhid: string
}

// ONLINE covers both the patient web app and WhatsApp self-service
// booking (both are the patient booking for themselves through a
// channel, not a staff/front-desk action) -- see migrations/0023 and
// app/api/patient_scheduling.py / app/api/scheduling.py's own
// booking_source reasoning. PHONE and STAFF_ASSISTED only make sense
// as a staff member's manual choice on the admin Book Appointment
// page; WALK_IN is that same choice, plus the trigger for the
// "Confirm & Check In" combined action.
export type BookingSource = 'ONLINE' | 'PHONE' | 'WALK_IN' | 'STAFF_ASSISTED'

export interface Department {
  id: number
  name: string
  active: boolean
}

// GET /doctors/{id}/departments -- a department already assigned to
// this doctor, with when it was assigned (doctor_departments.created_at,
// always present; see migrations/0001_baseline_schema.sql). Used to
// treat the earliest-assigned department as "primary" for display
// without a new is_primary column.
export interface DoctorDepartmentAssignment extends Department {
  assigned_at: string
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
  // Schedule tab's "Slot settings" panel (migrations/0022_doctor_slot_
  // settings.sql). default_duration_minutes only sizes the doctor-wide
  // slot preview and pre-fills a new appointment-type assignment's
  // duration -- real booking slot width still comes from the per-type
  // assignment. buffer_minutes is wired into actual slot generation
  // (app/services/availability_engine.get_available_slots).
  default_duration_minutes: number
  buffer_minutes: number
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
  default_duration_minutes: number
  buffer_minutes: number
  education: DoctorEducationEntry[]
}

export interface AppointmentType {
  id: number
  name: string
  duration_minutes: number
  active: boolean
  // Fee for this doctor/appointment-type pairing (patient arrival
  // workflow Phase 3) -- 0 means "not configured yet", never a
  // fabricated default.
  consultation_fee: number
}

export interface Slot {
  start_at: string
  end_at: string
}

// Date-First flow only: one entry of GET /web/availability/by-date's
// "doctors" list -- a doctor in the department offering the chosen
// appointment type, with their own real slots for the chosen date
// already attached. Unlike the WhatsApp equivalent, this can be
// present with an empty slots array (a zero-slot doctor shown as a
// disabled "Unavailable" card rather than omitted) -- total_slots is
// that day's fixed capacity, for the availability-color fullness
// ratio (see format.ts's formatAvailability).
export interface DoctorWithSlots extends DoctorProfileSummary {
  id: number
  name: string
  slots: Slot[]
  total_slots: number
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

// GET /appointment-types/admin -- the redesigned Appointment Types
// page's own row shape. doctor_count only ever counts an active
// doctor_appointment_types assignment to an active doctor (the same
// definition create_appointment_service/get_doctor_appointment_types
// already use for "actually assigned").
export interface AppointmentTypeAdminRow {
  id: number
  name: string
  active: boolean
  doctor_count: number
}

// GET /appointment-types/{id} -- the View drawer. duration_minutes/
// consultation_fee come straight from doctor_appointment_types, never
// duplicated onto the appointment type itself -- duration and fee stay
// a per-doctor concept.
export interface AppointmentTypeDoctorAssignment {
  doctor_id: number
  doctor_name: string
  department_name: string | null
  duration_minutes: number
  consultation_fee: number
}

export interface AppointmentTypeDetail {
  id: number
  name: string
  active: boolean
  doctor_count: number
  doctors: AppointmentTypeDoctorAssignment[]
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
  created_at: string
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
  // Assigned once payment succeeds or is waived (patient arrival
  // workflow Phase 4) -- NOT at check-in itself; null until then.
  token_number: number | null
  // Raw UTC instant (not converted to the doctor's timezone, unlike
  // start_at/end_at above) -- an audit-log-style "when was this
  // booked" moment, meant to be shown in the viewer's own local time
  // via format.ts's formatDateTime, the same convention as a doctor's
  // created_at elsewhere in this app.
  created_at: string
  // Payment fields (patient arrival workflow Phase 3). consultation_fee
  // is the *current* configured fee for this doctor/type (looked up
  // live, not frozen) -- payment_amount is what was actually
  // charged/waived at the time payment_status last changed, which can
  // differ from consultation_fee if pricing changed since.
  payment_status: 'UNPAID' | 'PAID' | 'FAILED' | 'WAIVED' | 'REFUNDED'
  consultation_fee: number
  payment_method: 'CASH' | 'UPI' | 'CARD' | 'OTHER' | null
  payment_amount: number | null
  paid_at: string | null
  waive_reason: string | null
  // Physical arrival time (migrations/0023), doctor-local like
  // start_at/end_at -- distinct from visited_at's role (there is no
  // separate visited_at field here; CHECKED_IN + this being set is
  // "formally checked in"). Can be set while status is still
  // CONFIRMED and start_at is still in the future: that's "arrived
  // early." See format.ts's describeArrival().
  arrived_at: string | null
  booking_source: BookingSource | null
  // Refund fields (migrations/0025) -- null until record_refund_service
  // runs. refund_amount can be less than payment_amount (a partial
  // refund); payment_amount itself is left untouched by a refund, so
  // both are visible at once.
  refund_amount: number | null
  refund_reason: string | null
  refunded_at: string | null
  // Permanent per-appointment identifier (migrations/0026), e.g.
  // "INV-00000123" -- always present, independent of payment_status.
  invoice_number: string
}

// GET /appointments/{id}/invoice -- consultation_fee plus any ad-hoc
// invoice_line_items (migrations/0026), and the total record_payment_
// service actually charges.
export interface InvoiceLineItem {
  id: number
  description: string
  amount: number
  added_by: number
  created_at: string
}

export interface Invoice {
  appointment_id: number
  invoice_number: string
  consultation_fee: number
  line_items: InvoiceLineItem[]
  extra_charges_total: number
  total_due: number
}

// GET /dashboard/billing -- see app/api/dashboard.py's own docstring on
// this endpoint for why each section has the scope it has (collections:
// trailing window; outstanding: current state; waivers/refunds:
// trailing window).
export interface BillingReport {
  window_days: number
  collections_by_method: { method: string | null; count: number; amount: number }[]
  collections_by_doctor: { doctor_id: number; doctor_name: string; count: number; amount: number }[]
  total_collected: number
  outstanding_unpaid: {
    appointment_id: number
    patient_name: string
    doctor_name: string
    payment_status: string
    visited_at: string | null
  }[]
  waivers: {
    count: number
    records: {
      appointment_id: number
      patient_name: string
      doctor_name: string
      reason: string | null
      waived_at: string | null
    }[]
  }
  refunds: {
    count: number
    total_refunded: number
    records: {
      appointment_id: number
      patient_name: string
      doctor_name: string
      refund_amount: number
      refund_reason: string | null
      refunded_at: string | null
    }[]
  }
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
  booking_source?: BookingSource | null
}

// POST /appointments/{id}/arrive -- mark_arrived_service's own shape,
// distinct from ArrivalActionResult below (no arrival_kind here: this
// endpoint only ever records a physical arrival, it never itself
// reaches CHECKED_IN).
export interface MarkArrivedResult {
  id: number
  status: string
  arrived_at: string
  start_at: string
  // False on an idempotent replay (arrived_at was already set) --
  // matches generate_queue_token_service's own newly_generated flag.
  newly_recorded: boolean
}

// POST /appointments/{id}/confirm-and-checkin's shape -- arrival_kind
// says which of the two actually happened (mark_visited_service's
// guard may have made it fall back to "arrived_early" instead of a
// real check-in; see confirm_and_check_in_service's docstring).
export interface ArrivalActionResult {
  id: number
  status: string
  arrival_kind: 'checked_in' | 'arrived_early'
  visited_at: string | null
  arrived_at: string | null
  start_at?: string
}

// POST /appointments/{id}/payment and /waive-payment (patient arrival
// workflow Phases 3-4) share this response shape.
export interface PaymentActionResult {
  id: number
  payment_status: 'UNPAID' | 'PAID' | 'FAILED' | 'WAIVED' | 'REFUNDED'
  payment_method: 'CASH' | 'UPI' | 'CARD' | 'OTHER' | null
  payment_amount: number | null
  payment_recorded_at: string | null
  waive_reason: string | null
  token_number: number | null
  refund_amount: number | null
  refund_reason: string | null
  refunded_at: string | null
  // True only when this call is what just generated the token (not an
  // idempotent replay) -- lets the UI show the "you're in the queue"
  // confirmation exactly once. Absent from record_refund_service's
  // response (POST .../refund-payment) -- a refund never touches queue
  // tokens, so there's nothing for this flag to mean there.
  token_just_issued?: boolean
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

// GET /api/exceptions (OPD/HIMS master spec Phase 11, sections 46-47) --
// live-computed operational alerts, one shape per type-specific field
// alongside the five fields every exception carries (what/why/who/
// recommended action/status). See app/services/exception_engine.py.
export type ExceptionType =
  | 'WAITING_FOR_TRIAGE'
  | 'WAITING_FOR_DOCTOR'
  | 'ORDER_PENDING'
  | 'PRESCRIPTION_NOT_DISPENSED'
  | 'BILLING_NOT_STARTED'
  | 'PAYMENT_PENDING'

export interface OperationalException {
  type: ExceptionType
  patient_id: number
  patient_name: string
  appointment_id?: number
  doctor_id?: number
  doctor_name?: string
  encounter_id?: number
  order_id?: number
  order_type?: string
  priority?: string
  prescription_id?: number
  balance?: number
  detected_at: string
  age_minutes: number
  what_happened: string
  why_it_matters: string
  who_should_act: string
  recommended_action: string
  current_status: string
}

export interface ExceptionsResponse {
  exceptions: OperationalException[]
  count: number
}

// GET /api/doctors/{id}/queue -- today's walk-in queue (migrations/0012,
// held/is_priority added by migrations/0027).
export interface QueueEntry {
  appointment_id: number
  token_number: number
  visited_at: string
  patient_id: number
  patient_name: string
  is_priority: boolean
}

export interface DoctorQueue {
  doctor_id: number
  doctor_name: string
  date: string
  now_serving: QueueEntry | null
  waiting: QueueEntry[]
  // Held via POST .../queue/hold (migrations/0027) -- skipped without
  // losing their place, shown separately so staff can find and recall
  // them. Excluded from now_serving/waiting.
  held: QueueEntry[]
  completed: QueueEntry[]
}

// OPD/HIMS master spec Phase 5 (migrations/0029_vitals_and_
// consultations.sql) -- GET /api/appointments/{id}/encounter.
export interface EncounterSummary {
  encounter_id: number
  encounter_status: 'OPEN' | 'CLOSED'
  opened_at: string
  closed_at: string | null
  patient_id: number
  patient_name: string
  patient_uhid: string
  patient_date_of_birth: string | null
  patient_gender: string | null
  doctor_id: number
  doctor_name: string
  token_number: number | null
  appointment_id: number
  appointment_status: string
  start_at: string
}

export type VitalsPriority = 'ROUTINE' | 'URGENT' | 'EMERGENCY'

export interface Vitals {
  id: number
  encounter_id: number
  recorded_by: number
  bp_systolic: number | null
  bp_diastolic: number | null
  pulse: number | null
  temperature_celsius: number | null
  spo2: number | null
  respiratory_rate: number | null
  weight_kg: number | null
  height_cm: number | null
  bmi: number | null
  pain_score: number | null
  chief_complaint: string | null
  priority: VitalsPriority
  nursing_notes: string | null
  recorded_at: string
}

export type VitalsInput = Partial<
  Omit<Vitals, 'id' | 'encounter_id' | 'recorded_by' | 'bmi' | 'recorded_at' | 'priority'>
> & { priority?: VitalsPriority }

export type ConsultationStatus = 'DRAFT' | 'COMPLETED'

export interface Consultation {
  id: number
  encounter_id: number
  doctor_id: number
  status: ConsultationStatus
  chief_complaint: string | null
  history_notes: string | null
  examination_notes: string | null
  diagnosis: string | null
  clinical_notes: string | null
  follow_up_date: string | null
  follow_up_reason: string | null
  started_at: string
  completed_at: string | null
}

export type ConsultationInput = Partial<
  Omit<Consultation, 'id' | 'encounter_id' | 'doctor_id' | 'status' | 'started_at' | 'completed_at'>
>

// OPD/HIMS master spec Phase 6 (migrations/0030_orders.sql) -- the
// order spine. One shape for every order type; order_type is what
// distinguishes a lab test from a radiology study from an external
// referral, not a separate interface per type.
export type OrderType = 'LAB' | 'RADIOLOGY' | 'PROCEDURE' | 'SERVICE' | 'EXTERNAL_REFERRAL'
export type OrderPriority = 'ROUTINE' | 'URGENT' | 'STAT'
export type OrderStatus = 'ORDERED' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED'

// OPD/HIMS master spec Phase 7 (migrations/0031_order_results.sql).
// One shape for both a lab panel's individual values (parameter e.g.
// "Hemoglobin", unit/reference_range meaningful) and a radiology
// report's narrative sections (parameter e.g. "Findings", unit/
// reference_range left null) -- see that migration's header.
export interface OrderResultItem {
  id: number
  order_id: number
  parameter: string
  result_value: string
  unit: string | null
  reference_range: string | null
  is_abnormal: boolean
  is_critical: boolean
  sequence: number
  recorded_by: number
  recorded_at: string
}

export interface OrderResultItemInput {
  parameter: string
  result_value: string
  unit?: string
  reference_range?: string
  is_abnormal?: boolean
  is_critical?: boolean
}

export interface ClinicalOrder {
  id: number
  encounter_id: number
  order_type: OrderType
  description: string
  clinical_indication: string | null
  priority: OrderPriority
  status: OrderStatus
  external_destination: string | null
  result_text: string | null
  ordering_doctor_id: number
  created_by: number
  cancelled_by: number | null
  cancel_reason: string | null
  ordered_at: string
  completed_at: string | null
  cancelled_at: string | null
  results: OrderResultItem[]
}

export interface OrderInput {
  order_type: OrderType
  description: string
  clinical_indication?: string
  priority?: OrderPriority
  external_destination?: string
}

// GET /api/public/queue-display -- the unauthenticated waiting-room
// board (app/api/queue_display.py). Deliberately just a doctor name and
// a bare token number, nothing patient-identifying -- see that file's
// own docstring for why.
export interface QueueDisplayEntry {
  doctor_id: number
  doctor_name: string
  now_serving_token: number | null
}

// OPD/HIMS master spec Phase 8 (migrations/0032_prescriptions_and_
// pharmacy.sql) -- prescription + pharmacy.
export type PrescriptionStatus = 'DRAFT' | 'PRESCRIBED' | 'CANCELLED'
export type DispenseStatus = 'PENDING' | 'PARTIALLY_DISPENSED' | 'DISPENSED'

export interface PharmacyDispenseRecord {
  id: number
  prescription_item_id: number
  pharmacy_stock_id: number | null
  quantity: number
  unit_price: number
  amount: number
  dispensed_by: number
  dispensed_at: string
}

export interface PrescriptionItem {
  id: number
  prescription_id: number
  medicine_name: string
  generic_name: string | null
  dosage: string | null
  route: string | null
  frequency: string | null
  duration: string | null
  quantity: number
  quantity_dispensed: number
  food_instructions: string | null
  special_instructions: string | null
  dispense_status: DispenseStatus
  // Only present on the item returned directly by the dispense
  // endpoint itself -- confirms what that one action just did.
  dispense_record?: PharmacyDispenseRecord
}

export interface PrescriptionItemInput {
  medicine_name: string
  generic_name?: string
  dosage?: string
  route?: string
  frequency?: string
  duration?: string
  quantity: number
  food_instructions?: string
  special_instructions?: string
}

export interface Prescription {
  id: number
  encounter_id: number
  doctor_id: number
  status: PrescriptionStatus
  notes: string | null
  prescribed_at: string | null
  cancelled_by: number | null
  cancel_reason: string | null
  cancelled_at: string | null
  created_by: number
  created_at: string
  updated_at: string
  items: PrescriptionItem[]
}

export interface PharmacyQueueEntry {
  prescription_id: number
  prescribed_at: string
  doctor_id: number
  doctor_name: string
  patient_id: number
  patient_name: string
  patient_uhid: string
  appointment_id: number
  items: PrescriptionItem[]
}

export interface PharmacyStockBatch {
  id: number
  medicine_name: string
  batch_number: string
  expiry_date: string
  quantity_on_hand: number
  unit_price: number
  active: boolean
  created_by: number
  created_at: string
  updated_at: string
}

export interface PharmacyStockInput {
  medicine_name: string
  batch_number: string
  expiry_date: string
  quantity_on_hand: number
  unit_price: number
}

// OPD/HIMS master spec Phase 9 (migrations/0033_billing_invoices.sql)
// -- a new, encounter-scoped invoice/charge/payment model, deliberately
// separate from the existing Invoice/InvoiceLineItem above (the
// consultation_fee/payment_status flow). Named "Bill"/"Charge"/"Payment"
// here (not "Invoice") to avoid colliding with those existing types --
// see app/api/billing.py's module docstring for why the API routes
// themselves use /bill, not /invoice, for the same reason.
export type BillStatus = 'OPEN' | 'VOID'
export type ChargeStatus = 'ACTIVE' | 'VOIDED'
export type ChargeSourceType = 'CONSULTATION' | 'LAB' | 'RADIOLOGY' | 'PROCEDURE' | 'SERVICE' | 'PHARMACY' | 'OTHER'
export type BillPaymentStatus = 'UNPAID' | 'PARTIALLY_PAID' | 'PAID'
export type BillPaymentMethod = 'CASH' | 'UPI' | 'CARD' | 'BANK_TRANSFER' | 'INSURANCE' | 'OTHER'
export type BillPaymentRecordStatus = 'COMPLETED' | 'VOIDED'

export interface BillCharge {
  id: number
  invoice_id: number
  description: string
  amount: number
  source_type: ChargeSourceType
  source_order_id: number | null
  source_dispense_id: number | null
  status: ChargeStatus
  voided_by: number | null
  void_reason: string | null
  voided_at: string | null
  created_by: number
  created_at: string
  updated_at: string
}

export interface BillChargeInput {
  description: string
  amount: number
  source_type?: ChargeSourceType
  source_order_id?: number
  source_dispense_id?: number
}

export interface BillPayment {
  id: number
  invoice_id: number
  receipt_number: string
  amount: number
  method: BillPaymentMethod
  transaction_id: string | null
  status: BillPaymentRecordStatus
  refunded_amount: number
  refund_reason: string | null
  refunded_by: number | null
  refunded_at: string | null
  voided_by: number | null
  void_reason: string | null
  voided_at: string | null
  recorded_by: number
  recorded_at: string
}

export interface BillPaymentInput {
  amount: number
  method: BillPaymentMethod
  transaction_id?: string
}

// GET/PATCH .../bill -- the full invoice summary, with its charges/
// payments and the totals computed server-side by _compute_totals
// (discount applied before tax; balance/payment_status derived from
// ACTIVE charges and COMPLETED-minus-refunded payments, never stored).
export interface BillSummary {
  id: number
  encounter_id: number
  invoice_number: string
  discount_amount: number
  discount_reason: string | null
  tax_rate: number
  status: BillStatus
  voided_by: number | null
  void_reason: string | null
  voided_at: string | null
  created_by: number
  created_at: string
  updated_at: string
  charges: BillCharge[]
  payments: BillPayment[]
  gross_amount: number
  taxable_amount: number
  tax_amount: number
  net_amount: number
  paid_amount: number
  balance: number
  payment_status: BillPaymentStatus
}

export interface UnbilledOrder {
  order_id: number
  order_type: OrderType
  description: string
  priority: OrderPriority
}

export interface UnbilledDispense {
  dispense_id: number
  medicine_name: string
  quantity: number
  amount: number
  dispensed_at: string
}

export interface UnbilledSources {
  orders: UnbilledOrder[]
  dispenses: UnbilledDispense[]
}

// OPD/HIMS master spec Phase 10 (section 44) -- Patient 360 / unified
// timeline. GET /patients/{id}/timeline (app/services/patient_timeline_
// service.py), a read-only aggregation over the tables above, shaped by
// visit rather than as one flat event list. Each Timeline* type here is
// a deliberately leaner projection of its full counterpart above (e.g.
// TimelineConsultation omits created_by/updated_at) -- exactly the
// columns that service selects, not a duplicate of the full record.
export interface TimelineVitals {
  id: number
  encounter_id: number
  recorded_by: number
  bp_systolic: number | null
  bp_diastolic: number | null
  pulse: number | null
  temperature_celsius: number | null
  spo2: number | null
  respiratory_rate: number | null
  weight_kg: number | null
  height_cm: number | null
  bmi: number | null
  pain_score: number | null
  chief_complaint: string | null
  priority: VitalsPriority
  nursing_notes: string | null
  recorded_at: string
}

export interface TimelineConsultation {
  id: number
  encounter_id: number
  doctor_id: number
  status: 'DRAFT' | 'COMPLETED'
  chief_complaint: string | null
  history_notes: string | null
  examination_notes: string | null
  diagnosis: string | null
  clinical_notes: string | null
  follow_up_date: string | null
  follow_up_reason: string | null
  started_at: string
  completed_at: string | null
}

export interface TimelineOrderResult {
  id: number
  order_id: number
  parameter: string
  result_value: string
  unit: string | null
  reference_range: string | null
  is_abnormal: boolean
  is_critical: boolean
  sequence: number
  recorded_at: string
}

export interface TimelineOrder {
  id: number
  encounter_id: number
  order_type: OrderType
  description: string
  clinical_indication: string | null
  priority: OrderPriority
  status: 'ORDERED' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED'
  external_destination: string | null
  result_text: string | null
  ordering_doctor_id: number
  cancel_reason: string | null
  ordered_at: string
  completed_at: string | null
  cancelled_at: string | null
  results: TimelineOrderResult[]
}

export interface TimelineDispense {
  id: number
  prescription_item_id: number
  quantity: number
  unit_price: number
  amount: number
  dispensed_at: string
}

export interface TimelinePrescriptionItem {
  id: number
  prescription_id: number
  medicine_name: string
  generic_name: string | null
  dosage: string | null
  route: string | null
  frequency: string | null
  duration: string | null
  quantity: number
  quantity_dispensed: number
  food_instructions: string | null
  special_instructions: string | null
  dispenses: TimelineDispense[]
}

export interface TimelinePrescription {
  id: number
  encounter_id: number
  doctor_id: number
  status: PrescriptionStatus
  notes: string | null
  prescribed_at: string | null
  cancel_reason: string | null
  cancelled_at: string | null
  items: TimelinePrescriptionItem[]
}

export interface TimelineCharge {
  id: number
  invoice_id: number
  description: string
  amount: number
  source_type: ChargeSourceType
  status: ChargeStatus
  created_at: string
}

export interface TimelinePayment {
  id: number
  invoice_id: number
  receipt_number: string
  amount: number
  method: BillPaymentMethod
  status: BillPaymentRecordStatus
  refunded_amount: number
  recorded_at: string
}

export interface TimelineInvoice {
  id: number
  encounter_id: number
  invoice_number: string
  discount_amount: number
  tax_rate: number
  status: BillStatus
  created_at: string
  charges: TimelineCharge[]
  payments: TimelinePayment[]
}

export interface TimelineVisit {
  encounter_id: number
  status: 'OPEN' | 'CLOSED'
  started_at: string
  closed_at: string | null
  doctor_id: number
  doctor_name: string
  appointment_id: number | null
  token_number: number | null
  appointment_status: string | null
  appointment_type_name: string | null
  vitals: TimelineVitals[]
  consultation: TimelineConsultation | null
  orders: TimelineOrder[]
  prescription: TimelinePrescription | null
  invoice: TimelineInvoice | null
}

export interface PatientTimeline {
  patient_id: number
  patient_name: string
  patient_uhid: string
  redirected_from: { patient_id: number; uhid: string } | null
  visits: TimelineVisit[]
}
