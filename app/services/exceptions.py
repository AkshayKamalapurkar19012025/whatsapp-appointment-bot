"""
Typed exceptions raised by app/services/*.

These are transport-agnostic on purpose: the service functions in
app/services/appointment_services.py don't know whether they're being
called from a FastAPI REST endpoint (app/api/appointments.py), the
WhatsApp conversational flow (app/api/scheduling.py), or a future web
endpoint -- each caller catches the specific exceptions it cares about
and translates them into whatever response shape it needs (an
HTTPException with a status code for REST, a conversational message for
WhatsApp, etc). This is what lets Web and WhatsApp share one
implementation of the scheduling rules without duplicating them.
"""


class ServiceError(Exception):
    """Base class for every exception in this module."""


class DoctorNotFound(ServiceError):
    pass


class PatientNotFound(ServiceError):
    pass


class AppointmentTypeNotAssigned(ServiceError):
    pass


class OutsideDoctorSchedule(ServiceError):
    pass


class DoctorBlockConflict(ServiceError):
    pass


class SlotOverlap(ServiceError):
    pass


class OutsideSchedulingWindow(ServiceError):
    pass


class AppointmentNotFound(ServiceError):
    pass


class AlreadyCancelled(ServiceError):
    pass


class NotAppointmentOwner(ServiceError):
    pass


class InvalidStatusTransition(ServiceError):
    """Raised by confirm/reject/mark_visited/mark_completed_service when
    the appointment's current status doesn't allow the requested
    transition (e.g. rejecting one that's already Confirmed, or marking
    Completed one that was never marked Visited)."""
    pass


class AppointmentNotStarted(ServiceError):
    """Raised by mark_visited_service when the appointment's status
    allows the transition (it's Confirmed) but its scheduled start_at is
    still in the future -- a patient can't be checked in for a visit
    that hasn't begun yet. Deliberately distinct from
    InvalidStatusTransition: the status itself is fine, it's just too
    early."""
    pass


class AppointmentSlotPassed(ServiceError):
    """Raised by confirm_appointment_service when the Pending
    appointment's status allows the transition but its scheduled
    start_at has already gone by -- there's no live slot left to confirm
    the patient into. The mirror image of AppointmentNotStarted (too
    late instead of too early), and just as deliberately distinct from
    InvalidStatusTransition: the status (PENDING) is fine, the slot just
    isn't current any more."""
    pass


class QueueEntryNotInQueue(ServiceError):
    """Raised by hold_queue_entry_service/recall_queue_entry_service/
    set_priority_service when the target appointment isn't actually a
    live queue entry -- not CHECKED_IN, or CHECKED_IN but never issued a
    token (see generate_queue_token_service's payment/waiver gate).
    Holding, recalling, or prioritizing only makes sense for someone
    who's actually in the doctor's queue today."""
    pass


class QueueEntryNotHeld(ServiceError):
    """Raised by recall_queue_entry_service when the target appointment
    isn't currently held -- recalling something that was never skipped
    is almost certainly a stale click against a queue view that's moved
    on, not a real intent."""
    pass


class PriorityReasonRequired(ServiceError):
    """Raised by set_priority_service when turning priority on without a
    reason. The whole point of a priority override is that it's
    accountable -- who did it, and why -- not a silent queue-jump."""
    pass


class PaymentStateConflict(ServiceError):
    """Raised by record_payment_service/waive_consultation_fee_service
    when payment_status is already in a state that makes the requested
    action nonsensical: paying a WAIVED/REFUNDED encounter, or waiving
    one that's already PAID. Distinct from InvalidStatusTransition,
    which gates on appointments.status (CHECKED_IN or not), not
    payment_status."""
    pass


class WaiverNotEligible(ServiceError):
    """Raised by waive_consultation_fee_service when the patient has no
    COMPLETED visit with this same doctor in the 3 calendar days before
    this check-in -- the clinic's waiver policy requires a genuine
    recent revisit, not just staff discretion. Not overridable by role:
    even an ADMIN cannot waive without a qualifying prior visit."""
    pass


class FreeVisitNotEligible(ServiceError):
    """Raised by settle_free_visit_service when the appointment's
    configured consultation_fee is not zero -- this endpoint only ever
    auto-settles a visit with no fee configured; a real, nonzero fee
    must go through record_payment_service (or, if the clinic's revisit
    policy applies, waive_consultation_fee_service), never this
    shortcut."""
    pass


class RefundExceedsPayment(ServiceError):
    """Raised by record_refund_service when the requested refund_amount
    is greater than the appointment's recorded payment_amount -- a
    refund can never return more money than was actually collected."""
    pass


# ---------------------------------------------------------------------
# Patient authentication (WEB P2) -- see app/services/patient_auth.py.
# ---------------------------------------------------------------------

class OtpRateLimited(ServiceError):
    pass


class OtpNotFound(ServiceError):
    pass


class OtpExpired(ServiceError):
    pass


class OtpAlreadyUsed(ServiceError):
    pass


class OtpLocked(ServiceError):
    pass


class OtpInvalid(ServiceError):
    pass


class RegistrationRequired(ServiceError):
    """Not a failure -- the OTP was verified correctly, but no patient
    exists for this number yet and no name was supplied to register one.
    The OTP row is deliberately left unconsumed so the caller can retry
    the same request with a name before it expires."""
    pass


class InvalidSession(ServiceError):
    pass


# ---------------------------------------------------------------------
# Staff/admin authentication (WEB P5) -- see app/services/staff_auth.py.
# ---------------------------------------------------------------------

class InvalidCredentials(ServiceError):
    """Unknown username OR a wrong password for a known username --
    deliberately the same exception for both, so a failed login never
    discloses whether a given username exists."""
    pass


class StaffAccountLocked(ServiceError):
    pass


class StaffAccountInactive(ServiceError):
    """Correct username and password, but the account has been
    deactivated. Deliberately distinct from InvalidCredentials -- see
    app/services/staff_auth.py's login() docstring for why this one is
    allowed to be distinguishable."""
    pass


class UsernameAlreadyExists(ServiceError):
    pass


class StaffNotFound(ServiceError):
    pass


# ---------------------------------------------------------------------
# Clinical (OPD/HIMS master spec Phase 5) -- see
# app/services/clinical_services.py.
# ---------------------------------------------------------------------

class EncounterNotFound(ServiceError):
    """No encounter exists for the given appointment -- shouldn't happen
    for any appointment created after migrations/0028_encounters.sql,
    but kept as a real, checked error rather than an assumption."""
    pass


class EncounterClosed(ServiceError):
    """Raised when recording vitals or writing to a consultation against
    an encounter whose care episode has already ended (the appointment
    reached a terminal status -- see _close_encounter_for_appointment).
    A closed encounter's clinical record is done; a new one belongs to a
    new visit, not an edit of the old one."""
    pass


class ConsultationAlreadyCompleted(ServiceError):
    """Raised by save_consultation_draft_service when the consultation
    is already COMPLETED -- no amendment workflow exists yet (master
    spec section 70), so a completed consultation's fields are frozen."""
    pass


class ConsultationIncomplete(ServiceError):
    """Raised by complete_consultation_service when required fields
    (chief complaint, diagnosis) are still empty -- the one clinical
    safety floor this phase enforces: a consultation can't be marked
    done with nothing actually documented."""
    pass


# ---------------------------------------------------------------------
# Orders (OPD/HIMS master spec Phase 6) -- see
# app/services/order_services.py.
# ---------------------------------------------------------------------

class ExternalReferralDestinationRequired(ServiceError):
    """Raised by create_order_service when order_type is
    EXTERNAL_REFERRAL and no destination was given -- the one field that
    makes an external referral meaningful (master spec section 31: "CBC,
    Destination: External Laboratory"). Checked in the service layer,
    not left to the DB CHECK constraint alone, matching this codebase's
    convention (migrations/0027's own header comment) of keeping
    business rules in the service layer with the DB constraint as a
    backstop, not the primary enforcement."""
    pass


class OrderNotFound(ServiceError):
    pass


class OrderNotCancellable(ServiceError):
    """Raised by cancel_order_service when the order is already
    COMPLETED or CANCELLED -- a finished or already-cancelled order has
    nothing left to cancel."""
    pass


# ---------------------------------------------------------------------
# Diagnostics / order results (OPD/HIMS master spec Phase 7) -- see
# app/services/order_services.py's record_order_result_service.
# ---------------------------------------------------------------------

class OrderNotResultable(ServiceError):
    """Raised by record_order_result_service when the order is already
    COMPLETED (results already recorded -- no amendment workflow yet,
    see migrations/0031's header) or CANCELLED (nothing to result)."""
    pass


# ---------------------------------------------------------------------
# Prescription / pharmacy (OPD/HIMS master spec Phase 8) -- see
# app/services/pharmacy_services.py.
# ---------------------------------------------------------------------

class PrescriptionNotFound(ServiceError):
    pass


class PrescriptionAlreadyPrescribed(ServiceError):
    """Raised when adding/removing an item, or prescribing again, on a
    prescription that's already PRESCRIBED -- once signed and sent to
    pharmacy, the item set is frozen (no amendment workflow, same
    stance as consultations)."""
    pass


class PrescriptionEmpty(ServiceError):
    """Raised by prescribe_service when the prescription has no items
    -- nothing to send to pharmacy."""
    pass


class PrescriptionNotCancellable(ServiceError):
    """Raised by cancel_prescription_service when the prescription
    isn't PRESCRIBED (DRAFT has nothing sent to cancel; CANCELLED is
    already cancelled), or when any item already has a nonzero
    quantity_dispensed -- once medicine has actually been handed over,
    the prescription as a whole can no longer be cancelled (the
    individual undispensed items remain, but cancelling the whole
    prescription would misrepresent what already happened)."""
    pass


class PrescriptionItemNotFound(ServiceError):
    pass


class DispenseQuantityExceedsRemaining(ServiceError):
    """Raised by record_dispense_service when the requested quantity
    would dispense more than prescription_items.quantity - quantity_
    dispensed -- the DB CHECK constraint is the backstop, this is the
    service-layer check that raises a caller-friendly error first."""
    pass


class InsufficientStock(ServiceError):
    """Raised by record_dispense_service when a pharmacy_stock_id is
    given but its quantity_on_hand is less than the requested dispense
    quantity."""
    pass


class MedicineMismatch(ServiceError):
    """Raised by record_dispense_service when the given pharmacy_
    stock_id's medicine_name doesn't match the prescription item's --
    this is what "do not allow unauthorized substitution" (master spec
    section 36) means in a schema with no substitution feature at all:
    dispensing against the wrong stock row is a checked error, not
    merely an unbuilt button."""
    pass


class PrescriptionItemNotDispensable(ServiceError):
    """Raised by record_dispense_service when the item's parent
    prescription isn't PRESCRIBED (still DRAFT, or CANCELLED) --
    nothing should ever reach a pharmacist's queue before it's signed
    and sent, but this is checked regardless of which URL reaches this
    function."""
    pass


class PharmacyStockNotFound(ServiceError):
    pass


class DuplicateStockBatch(ServiceError):
    """Raised by create_pharmacy_stock_service when a row with the same
    (medicine_name, batch_number) already exists -- a genuine new
    shipment gets a new batch number; re-entering the same one is
    almost certainly a mistake, not a real restock, so this is refused
    rather than silently summed into the existing row's quantity."""
    pass


# ---------------------------------------------------------------------
# Billing (OPD/HIMS master spec Phase 9) -- see
# app/services/billing_services.py. A new, encounter-scoped invoice/
# charge/payment model, independent of appointments.payment_status --
# see migrations/0033_billing_invoices.sql's header for why.
# ---------------------------------------------------------------------

class InvoiceNotFound(ServiceError):
    pass


class InvoiceVoided(ServiceError):
    """Raised when adding a charge or recording a payment against an
    invoice that's already VOID -- a voided invoice is closed, the same
    way a cancelled prescription/order is."""
    pass


class InvoiceNotVoidable(ServiceError):
    """Raised by void_invoice_service when the invoice already has a
    non-voided payment recorded -- once real money has been received
    against an invoice, voiding the invoice itself would misrepresent
    what happened; individual charges/payments can still be voided or
    refunded, but not the invoice as a whole."""
    pass


class ChargeNotFound(ServiceError):
    pass


class ChargeAlreadyVoided(ServiceError):
    pass


class DuplicateCharge(ServiceError):
    """Raised by add_charge_service when the given source_order_id or
    source_dispense_id already has a charge linked to it -- an order or
    a dispense gets billed exactly once (the DB's partial unique
    indexes are the backstop; this is the caller-friendly version)."""
    pass


class InvalidChargeSource(ServiceError):
    """Raised by add_charge_service when the given source_order_id/
    source_dispense_id doesn't belong to this invoice's own encounter
    -- prevents billing one patient's visit for another's order/dispense
    via a guessed id."""
    pass


class PaymentNotFound(ServiceError):
    pass


class PaymentAlreadyVoided(ServiceError):
    pass


class PaymentExceedsBalance(ServiceError):
    """Raised by record_invoice_payment_service when the requested
    amount is more than the invoice's current outstanding balance --
    this is also what makes a duplicate-click on "record payment"
    self-correcting: the first click settles the balance to zero, so a
    second one for the same amount is refused rather than silently
    accepted as a second, real payment."""
    pass


class DuplicateTransactionId(ServiceError):
    """Raised by record_invoice_payment_service when the given
    transaction_id already belongs to another payment (any invoice) --
    the actual "prevent duplicate payments" mechanism (master spec
    section 41) for any method with a real external reference to check
    against (UPI/card/bank transfer); the DB's partial unique index is
    the backstop, this is the caller-friendly version."""
    pass


class PaymentRefundExceedsAmount(ServiceError):
    """Raised by refund_invoice_payment_service when the requested
    refund would exceed what's left to refund on this specific payment
    (amount - refunded_amount already recorded)."""
    pass


# ---------------------------------------------------------------------
# Patient merge/unmerge (M8) -- see app/services/patient_merge.py.
# ---------------------------------------------------------------------

class CannotMergePatientIntoItself(ServiceError):
    pass


class PatientAlreadyMerged(ServiceError):
    """Raised when either patient named in a merge request already has
    merged_into_id set -- a retired identity can't be merged again, and
    can't absorb another patient either. Merge the *surviving* patient
    from that earlier merge instead."""
    pass


class MergeNotFound(ServiceError):
    pass


class DuplicateReviewNotFound(ServiceError):
    """Raised for an unknown review id, or one that's already been
    decided -- a decision is recorded once, never overwritten."""
    pass


class UnmergeNotPermitted(ServiceError):
    """Raised when a clinical record (appointment or encounter) exists
    for the surviving patient with created_at after the merge's own
    created_at -- there's no way to tell whether it belongs to the
    surviving identity or the retired one, so unmerge refuses rather
    than guessing."""
    pass
