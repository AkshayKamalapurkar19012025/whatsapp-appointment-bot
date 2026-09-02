"""
Typed exceptions raised by app/services/*.

These are transport-agnostic on purpose: the service functions in
app/services/appointment_services.py don't know whether they're being
called from a FastAPI REST endpoint (app/api/appointments.py), the
WhatsApp conversational flow (app/api/booking.py), or a future web
endpoint -- each caller catches the specific exceptions it cares about
and translates them into whatever response shape it needs (an
HTTPException with a status code for REST, a conversational message for
WhatsApp, etc). This is what lets Web and WhatsApp share one
implementation of the booking rules without duplicating them.
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


class OutsideBookingWindow(ServiceError):
    pass


class AppointmentNotFound(ServiceError):
    pass


class AlreadyCancelled(ServiceError):
    pass


class NotAppointmentOwner(ServiceError):
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
