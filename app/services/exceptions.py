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
