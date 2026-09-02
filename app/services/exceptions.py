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


class AppointmentServiceError(Exception):
    """Base class for every exception in this module."""


class DoctorNotFound(AppointmentServiceError):
    pass


class PatientNotFound(AppointmentServiceError):
    pass


class AppointmentTypeNotAssigned(AppointmentServiceError):
    pass


class OutsideDoctorSchedule(AppointmentServiceError):
    pass


class DoctorBlockConflict(AppointmentServiceError):
    pass


class SlotOverlap(AppointmentServiceError):
    pass


class OutsideBookingWindow(AppointmentServiceError):
    pass


class AppointmentNotFound(AppointmentServiceError):
    pass


class AlreadyCancelled(AppointmentServiceError):
    pass


class NotAppointmentOwner(AppointmentServiceError):
    pass
