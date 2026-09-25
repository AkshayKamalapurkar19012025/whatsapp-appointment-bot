"""
OPD/HIMS interoperability master prompt Phase 8 (FHIR Foundation): a
minimal, read-only FHIR R4 interoperability layer over this
application's existing HIMS domain model. See
docs/architecture/FHIR_FOUNDATION.md for the full architecture,
resource-mapping table, and everything deliberately not built.

Deliberately isolated from /api/... (every other router in this app):
mounted at /fhir/r4/... directly on the app, not nested under /api, so
this interoperability boundary is a visibly separate surface from the
application's own internal REST API the frontend calls -- an external
FHIR client hits this router; the SPA never does. Every endpoint here
reuses the SAME staff-session authentication and hospital_id tenant
check every other endpoint in app/api/ already uses (Depends(
get_current_staff), then `AND hospital_id = %s` on every query) -- no
second auth/authorization mechanism, per this phase's own explicit
instruction. Read-only: no POST/PUT/PATCH/DELETE route exists here, and
none is planned until write support is a separately-scoped future
phase.

Every resource-level error this router's own endpoint code produces
(not found; exists, but in a different hospital) returns a FHIR
OperationOutcome body -- returned directly as a JSONResponse rather
than a raised HTTPException, so the global exception handler
(app/error_handling.py, which wraps ordinary HTTPExceptions in this
app's own {success, errorCode, message, details} envelope) never
touches it. A resource that doesn't exist and a resource that exists in
a *different* hospital both produce the identical 404 (same "don't leak
existence across tenants" discipline every other app/api/ single-record
GET already follows, e.g. app/api/patients.py's get_patient).
Authentication failures (401, no/invalid session) are raised by the
shared get_current_staff dependency itself, before any endpoint here
runs, and keep that dependency's own existing response shape unchanged
-- per this phase's own instruction, FHIR authentication IS the
existing HIMS authentication, error format included, not a
FHIR-flavoured re-wrap of it.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services.fhir_mappers import (
    allergy_to_fhir,
    appointment_to_fhir,
    condition_to_fhir,
    encounter_to_fhir,
    medication_request_to_fhir,
    medication_to_fhir,
    observation_from_order_result_to_fhir,
    organization_to_fhir,
    patient_to_fhir,
    practitioner_to_fhir,
    service_request_to_fhir,
)
from app.services.medication_services import _MEDICATION_COLUMNS, _medication_row_to_dict

router = APIRouter(prefix="/fhir/r4", tags=["FHIR"])

_FHIR_MEDIA_TYPE = "application/fhir+json"


def _fhir_response(resource: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=resource, status_code=status_code, media_type=_FHIR_MEDIA_TYPE)


def _operation_outcome(status_code: int, severity: str, code: str, diagnostics: str) -> JSONResponse:
    return _fhir_response(
        {
            "resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": code, "diagnostics": diagnostics}],
        },
        status_code=status_code,
    )


def _not_found(resource_type: str, resource_id: str) -> JSONResponse:
    # Identical response whether the id doesn't exist at all or belongs
    # to a different hospital -- see module docstring.
    return _operation_outcome(404, "error", "not-found", f"{resource_type}/{resource_id} not found")


@router.get("/Patient/{patient_id}")
def get_fhir_patient(patient_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, whatsapp_number, date_of_birth, gender, uhid,
                       government_id, merged_into_id, email, address_line, city, state, pincode
                FROM patients
                WHERE id = %s AND hospital_id = %s
                """,
                (patient_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Patient", str(patient_id))

    columns = (
        "id", "name", "whatsapp_number", "date_of_birth", "gender", "uhid",
        "government_id", "merged_into_id", "email", "address_line", "city", "state", "pincode",
    )
    return _fhir_response(patient_to_fhir(dict(zip(columns, row))))


@router.get("/Practitioner/{doctor_id}")
def get_fhir_practitioner(doctor_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, active, qualifications FROM doctors WHERE id = %s AND hospital_id = %s",
                (doctor_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Practitioner", str(doctor_id))

    return _fhir_response(practitioner_to_fhir(dict(zip(("id", "name", "active", "qualifications"), row))))


@router.get("/Organization/{hospital_id}")
def get_fhir_organization(hospital_id: int, staff: dict = Depends(get_current_staff)):
    # A caller can only ever fetch their own hospital -- there is no
    # cross-hospital Organization lookup in a single-tenant-per-session
    # model, so the tenant check IS the id match itself.
    if hospital_id != staff["hospital_id"]:
        return _not_found("Organization", str(hospital_id))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, code, name FROM hospitals WHERE id = %s", (hospital_id,))
            row = cur.fetchone()

    if row is None:
        return _not_found("Organization", str(hospital_id))

    return _fhir_response(organization_to_fhir(dict(zip(("id", "code", "name"), row))))


@router.get("/Appointment/{appointment_id}")
def get_fhir_appointment(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.status, a.start_at, a.end_at, a.patient_id, a.doctor_id, t.name
                FROM appointments a
                JOIN appointment_types t ON t.id = a.appointment_type_id
                WHERE a.id = %s AND a.hospital_id = %s
                """,
                (appointment_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Appointment", str(appointment_id))

    columns = ("id", "status", "start_at", "end_at", "patient_id", "doctor_id", "appointment_type_name")
    return _fhir_response(appointment_to_fhir(dict(zip(columns, row))))


@router.get("/Encounter/{encounter_id}")
def get_fhir_encounter(encounter_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.status, e.encounter_type, e.patient_id, e.doctor_id,
                       e.started_at, e.closed_at, a.id
                FROM encounters e
                LEFT JOIN appointments a ON a.encounter_id = e.id
                WHERE e.id = %s AND e.hospital_id = %s
                """,
                (encounter_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Encounter", str(encounter_id))

    columns = (
        "id", "status", "encounter_type", "patient_id", "doctor_id", "started_at", "closed_at", "appointment_id",
    )
    return _fhir_response(encounter_to_fhir(dict(zip(columns, row))))


@router.get("/Condition/{consultation_id}")
def get_fhir_condition(consultation_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.diagnosis, c.diagnosis_code_system, c.diagnosis_code,
                       c.diagnosis_code_display, c.encounter_id, c.started_at, e.patient_id
                FROM consultations c
                JOIN encounters e ON e.id = c.encounter_id
                WHERE c.id = %s AND e.hospital_id = %s
                """,
                (consultation_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Condition", str(consultation_id))

    columns = (
        "id", "diagnosis", "diagnosis_code_system", "diagnosis_code",
        "diagnosis_code_display", "encounter_id", "started_at", "patient_id",
    )
    resource = condition_to_fhir(dict(zip(columns, row)))
    if resource is None:
        # A real consultation with no diagnosis documented yet -- there
        # is nothing to represent as a Condition (see fhir_mappers.py's
        # condition_to_fhir docstring), which is a 404 same as a
        # genuinely nonexistent id, not a 200 with an empty resource.
        return _not_found("Condition", str(consultation_id))
    return _fhir_response(resource)


@router.get("/AllergyIntolerance/{allergy_id}")
def get_fhir_allergy(allergy_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pa.id, pa.patient_id, pa.allergen, pa.reaction, pa.severity,
                       pa.active, pa.recorded_at
                FROM patient_allergies pa
                JOIN patients p ON p.id = pa.patient_id
                WHERE pa.id = %s AND p.hospital_id = %s
                """,
                (allergy_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("AllergyIntolerance", str(allergy_id))

    columns = ("id", "patient_id", "allergen", "reaction", "severity", "active", "recorded_at")
    return _fhir_response(allergy_to_fhir(dict(zip(columns, row))))


@router.get("/Medication/{medication_id}")
def get_fhir_medication(medication_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_MEDICATION_COLUMNS)} FROM medications WHERE id = %s AND hospital_id = %s",
                (medication_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Medication", str(medication_id))

    # Reuses medication_services.py's own row-to-dict (its computed
    # display_name is exactly what code.text below needs) rather than
    # re-deriving that formatting logic a second time here.
    return _fhir_response(medication_to_fhir(_medication_row_to_dict(row)))


@router.get("/MedicationRequest/{prescription_item_id}")
def get_fhir_medication_request(prescription_item_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pi.id, pi.medicine_name, pi.generic_name, pi.dosage, pi.route,
                       pi.frequency, pi.duration, pi.food_instructions, pi.quantity,
                       pi.quantity_dispensed, pi.medication_id,
                       p.status, p.encounter_id, p.doctor_id, e.patient_id
                FROM prescription_items pi
                JOIN prescriptions p ON p.id = pi.prescription_id
                JOIN encounters e ON e.id = p.encounter_id
                WHERE pi.id = %s AND e.hospital_id = %s
                """,
                (prescription_item_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("MedicationRequest", str(prescription_item_id))

    columns = (
        "id", "medicine_name", "generic_name", "dosage", "route", "frequency", "duration",
        "food_instructions", "quantity", "quantity_dispensed", "medication_id",
        "prescription_status", "encounter_id", "doctor_id", "patient_id",
    )
    return _fhir_response(medication_request_to_fhir(dict(zip(columns, row))))


@router.get("/Observation/{order_result_id}")
def get_fhir_observation(order_result_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.parameter, r.result_value, r.unit, r.unit_system, r.unit_code,
                       r.reference_range, r.is_abnormal, r.is_critical, r.recorded_at,
                       o.encounter_id, e.patient_id
                FROM order_results r
                JOIN orders o ON o.id = r.order_id
                JOIN encounters e ON e.id = o.encounter_id
                WHERE r.id = %s AND e.hospital_id = %s
                """,
                (order_result_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Observation", str(order_result_id))

    columns = (
        "id", "parameter", "result_value", "unit", "unit_system", "unit_code",
        "reference_range", "is_abnormal", "is_critical", "recorded_at", "encounter_id", "patient_id",
    )
    return _fhir_response(observation_from_order_result_to_fhir(dict(zip(columns, row))))


@router.get("/ServiceRequest/{order_id}")
def get_fhir_service_request(order_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.order_type, o.description, o.priority, o.status,
                       o.ordering_doctor_id, o.ordered_at, o.encounter_id, e.patient_id
                FROM orders o
                JOIN encounters e ON e.id = o.encounter_id
                WHERE o.id = %s AND e.hospital_id = %s
                """,
                (order_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("ServiceRequest", str(order_id))

    columns = (
        "id", "order_type", "description", "priority", "status",
        "ordering_doctor_id", "ordered_at", "encounter_id", "patient_id",
    )
    return _fhir_response(service_request_to_fhir(dict(zip(columns, row))))
