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
    observation_from_vitals_to_fhir,
    organization_to_fhir,
    patient_to_fhir,
    practitioner_role_to_fhir,
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


# OPD/HIMS interoperability master prompt Phase 9 (FHIR hardening): a
# hard cap on every search/$everything result set, not real pagination
# (no `link[]`/next-page support) -- this repo's real data volumes are
# tiny (dozens of rows per table, verified against the live dev DB
# before choosing this number) and every search here is already scoped
# to a single patient, so 50 is generous headroom, not a load-bearing
# performance tune; it exists only so a search endpoint can never return
# an unbounded result set, matching the same LIMIT 50 convention
# app/services/medication_services.py's list_medications_service
# already uses for its own search endpoint.
_SEARCH_LIMIT = 50


def _bundle(bundle_type: str, resources: list[dict]) -> dict:
    """Builds a minimal, spec-valid FHIR Bundle. `searchset` for the
    Patient?... search endpoints below, `collection` for $everything.
    Every entry is `resource` only (no `search.mode`/`fullUrl`) -- the
    smallest shape a Bundle can honestly take without inventing request
    URLs this server doesn't otherwise assign resources."""
    return {
        "resourceType": "Bundle",
        "type": bundle_type,
        "total": len(resources),
        "entry": [{"resource": r} for r in resources],
    }


def _find_patient(cur, patient_id: int, hospital_id: int) -> bool:
    cur.execute("SELECT 1 FROM patients WHERE id = %s AND hospital_id = %s", (patient_id, hospital_id))
    return cur.fetchone() is not None


@router.get("/Patient/{patient_id}")
def get_fhir_patient(patient_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, whatsapp_number, date_of_birth, gender, uhid,
                       government_id, merged_into_id, email, address_line, city, state, pincode,
                       updated_at
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
        "updated_at",
    )
    return _fhir_response(patient_to_fhir(dict(zip(columns, row))))


@router.get("/Patient")
def search_fhir_patients(identifier: str | None = None, staff: dict = Depends(get_current_staff)):
    # Smallest useful Patient search: exact-match on the one identifier
    # this system actually assigns and treats as permanent (uhid --
    # CLAUDE.md's "Patient is the permanent identity" principle; see
    # ADR-001). No name/DOB/other fuzzy search here -- that's the
    # existing internal /api/patients search endpoint's job, not this
    # read-only external interoperability surface's.
    if not identifier:
        return _fhir_response(_bundle("searchset", []))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, whatsapp_number, date_of_birth, gender, uhid,
                       government_id, merged_into_id, email, address_line, city, state, pincode,
                       updated_at
                FROM patients
                WHERE uhid = %s AND hospital_id = %s
                """,
                (identifier, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _fhir_response(_bundle("searchset", []))

    columns = (
        "id", "name", "whatsapp_number", "date_of_birth", "gender", "uhid",
        "government_id", "merged_into_id", "email", "address_line", "city", "state", "pincode",
        "updated_at",
    )
    return _fhir_response(_bundle("searchset", [patient_to_fhir(dict(zip(columns, row)))]))


@router.get("/Practitioner/{doctor_id}")
def get_fhir_practitioner(doctor_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, active, qualifications, hospital_id, updated_at
                FROM doctors WHERE id = %s AND hospital_id = %s
                """,
                (doctor_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Practitioner", str(doctor_id))

    columns = ("id", "name", "active", "qualifications", "hospital_id", "updated_at")
    return _fhir_response(practitioner_to_fhir(dict(zip(columns, row))))


@router.get("/PractitionerRole/{doctor_id}")
def get_fhir_practitioner_role(doctor_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, active, hospital_id, updated_at FROM doctors WHERE id = %s AND hospital_id = %s",
                (doctor_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("PractitionerRole", str(doctor_id))

    columns = ("id", "active", "hospital_id", "updated_at")
    return _fhir_response(practitioner_role_to_fhir(dict(zip(columns, row))))


@router.get("/Organization/{hospital_id}")
def get_fhir_organization(hospital_id: int, staff: dict = Depends(get_current_staff)):
    # A caller can only ever fetch their own hospital -- there is no
    # cross-hospital Organization lookup in a single-tenant-per-session
    # model, so the tenant check IS the id match itself.
    if hospital_id != staff["hospital_id"]:
        return _not_found("Organization", str(hospital_id))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, code, name, updated_at FROM hospitals WHERE id = %s", (hospital_id,))
            row = cur.fetchone()

    if row is None:
        return _not_found("Organization", str(hospital_id))

    return _fhir_response(organization_to_fhir(dict(zip(("id", "code", "name", "updated_at"), row))))


@router.get("/Appointment/{appointment_id}")
def get_fhir_appointment(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.status, a.start_at, a.end_at, a.patient_id, a.doctor_id, t.name,
                       a.updated_at
                FROM appointments a
                JOIN appointment_types t ON t.id = a.appointment_type_id
                WHERE a.id = %s AND a.hospital_id = %s
                """,
                (appointment_id, staff["hospital_id"]),
            )
            row = cur.fetchone()

    if row is None:
        return _not_found("Appointment", str(appointment_id))

    columns = (
        "id", "status", "start_at", "end_at", "patient_id", "doctor_id", "appointment_type_name", "updated_at",
    )
    return _fhir_response(appointment_to_fhir(dict(zip(columns, row))))


@router.get("/Encounter/{encounter_id}")
def get_fhir_encounter(encounter_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.status, e.encounter_type, e.patient_id, e.doctor_id,
                       e.started_at, e.closed_at, a.id, e.updated_at
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
        "updated_at",
    )
    return _fhir_response(encounter_to_fhir(dict(zip(columns, row))))


@router.get("/Condition/{consultation_id}")
def get_fhir_condition(consultation_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.diagnosis, c.diagnosis_code_system, c.diagnosis_code,
                       c.diagnosis_code_display, c.encounter_id, c.started_at, e.patient_id,
                       c.updated_at
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
        "diagnosis_code_display", "encounter_id", "started_at", "patient_id", "updated_at",
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
                       pi.frequency, pi.duration, pi.food_instructions, pi.special_instructions,
                       pi.quantity, pi.quantity_dispensed, pi.medication_id,
                       p.status, p.encounter_id, p.doctor_id, e.patient_id, pi.updated_at
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
        "food_instructions", "special_instructions", "quantity", "quantity_dispensed", "medication_id",
        "prescription_status", "encounter_id", "doctor_id", "patient_id", "updated_at",
    )
    return _fhir_response(medication_request_to_fhir(dict(zip(columns, row))))


def _fetch_order_result_observation(cur, result_id: int, hospital_id: int):
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
        (result_id, hospital_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    columns = (
        "id", "parameter", "result_value", "unit", "unit_system", "unit_code",
        "reference_range", "is_abnormal", "is_critical", "recorded_at", "encounter_id", "patient_id",
    )
    return observation_from_order_result_to_fhir(dict(zip(columns, row)))


def _fetch_vitals_observation(cur, vitals_id: int, hospital_id: int):
    cur.execute(
        """
        SELECT v.id, v.encounter_id, e.patient_id, v.recorded_at,
               v.bp_systolic, v.bp_diastolic, v.pulse, v.temperature_celsius, v.spo2,
               v.respiratory_rate, v.weight_kg, v.height_cm, v.bmi, v.pain_score
        FROM vitals v
        JOIN encounters e ON e.id = v.encounter_id
        WHERE v.id = %s AND e.hospital_id = %s
        """,
        (vitals_id, hospital_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    columns = (
        "id", "encounter_id", "patient_id", "recorded_at",
        "bp_systolic", "bp_diastolic", "pulse", "temperature_celsius", "spo2",
        "respiratory_rate", "weight_kg", "height_cm", "bmi", "pain_score",
    )
    return observation_from_vitals_to_fhir(dict(zip(columns, row)))


@router.get("/Observation/{observation_id}")
def get_fhir_observation(observation_id: str, staff: dict = Depends(get_current_staff)):
    # OPD/HIMS interoperability master prompt Phase 9: Observation is
    # sourced from two independent tables (order_results, vitals), each
    # with its own auto-increment id sequence, so the FHIR id carries a
    # source prefix (fhir_mappers.py's observation_from_order_result_to_
    # fhir/observation_from_vitals_to_fhir docstrings explain why) --
    # this endpoint dispatches on that prefix rather than guessing.
    if observation_id.startswith("or-"):
        raw_id, fetch = observation_id[3:], _fetch_order_result_observation
    elif observation_id.startswith("vt-"):
        raw_id, fetch = observation_id[3:], _fetch_vitals_observation
    else:
        return _not_found("Observation", observation_id)

    if not raw_id.isdigit():
        return _not_found("Observation", observation_id)

    with get_connection() as conn:
        with conn.cursor() as cur:
            resource = fetch(cur, int(raw_id), staff["hospital_id"])

    if resource is None:
        return _not_found("Observation", observation_id)
    return _fhir_response(resource)


@router.get("/ServiceRequest/{order_id}")
def get_fhir_service_request(order_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.order_type, o.description, o.priority, o.status,
                       o.ordering_doctor_id, o.ordered_at, o.encounter_id, e.patient_id, o.updated_at
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
        "ordering_doctor_id", "ordered_at", "encounter_id", "patient_id", "updated_at",
    )
    return _fhir_response(service_request_to_fhir(dict(zip(columns, row))))


# ---------------------------------------------------------------------
# Search (Phase 9) -- Patient?identifier= above; everything else here is
# ?patient=<id>, the one search parameter every implemented clinical/
# scheduling resource in this domain model can honestly answer, since
# every one of them already carries (directly or via its encounter) a
# patient_id. Each query below is hospital_id-scoped exactly like the
# matching single-resource GET above it, reuses the existing indexes
# those single-resource lookups already rely on (no new index added --
# see this repo's Phase 9 audit notes on real data volumes), and is
# capped at _SEARCH_LIMIT with no further pagination. A patient id from
# another hospital, or one that doesn't exist, silently produces an
# empty Bundle (the join itself yields zero rows) rather than a 404 --
# consistent with real-world FHIR search semantics (an unmatched search
# is empty, not an error) and it leaks nothing a 404 wouldn't already
# leak less of.
# ---------------------------------------------------------------------


@router.get("/Appointment")
def search_fhir_appointments(patient: int | None = None, staff: dict = Depends(get_current_staff)):
    if patient is None:
        return _fhir_response(_bundle("searchset", []))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.status, a.start_at, a.end_at, a.patient_id, a.doctor_id, t.name,
                       a.updated_at
                FROM appointments a
                JOIN appointment_types t ON t.id = a.appointment_type_id
                WHERE a.patient_id = %s AND a.hospital_id = %s
                ORDER BY a.start_at DESC
                LIMIT %s
                """,
                (patient, staff["hospital_id"], _SEARCH_LIMIT),
            )
            rows = cur.fetchall()

    columns = (
        "id", "status", "start_at", "end_at", "patient_id", "doctor_id", "appointment_type_name", "updated_at",
    )
    resources = [appointment_to_fhir(dict(zip(columns, row))) for row in rows]
    return _fhir_response(_bundle("searchset", resources))


@router.get("/Encounter")
def search_fhir_encounters(patient: int | None = None, staff: dict = Depends(get_current_staff)):
    if patient is None:
        return _fhir_response(_bundle("searchset", []))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.status, e.encounter_type, e.patient_id, e.doctor_id,
                       e.started_at, e.closed_at, a.id, e.updated_at
                FROM encounters e
                LEFT JOIN appointments a ON a.encounter_id = e.id
                WHERE e.patient_id = %s AND e.hospital_id = %s
                ORDER BY e.started_at DESC
                LIMIT %s
                """,
                (patient, staff["hospital_id"], _SEARCH_LIMIT),
            )
            rows = cur.fetchall()

    columns = (
        "id", "status", "encounter_type", "patient_id", "doctor_id", "started_at", "closed_at", "appointment_id",
        "updated_at",
    )
    resources = [encounter_to_fhir(dict(zip(columns, row))) for row in rows]
    return _fhir_response(_bundle("searchset", resources))


@router.get("/Condition")
def search_fhir_conditions(patient: int | None = None, staff: dict = Depends(get_current_staff)):
    if patient is None:
        return _fhir_response(_bundle("searchset", []))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.diagnosis, c.diagnosis_code_system, c.diagnosis_code,
                       c.diagnosis_code_display, c.encounter_id, c.started_at, e.patient_id,
                       c.updated_at
                FROM consultations c
                JOIN encounters e ON e.id = c.encounter_id
                WHERE e.patient_id = %s AND e.hospital_id = %s
                ORDER BY c.started_at DESC
                LIMIT %s
                """,
                (patient, staff["hospital_id"], _SEARCH_LIMIT),
            )
            rows = cur.fetchall()

    columns = (
        "id", "diagnosis", "diagnosis_code_system", "diagnosis_code",
        "diagnosis_code_display", "encounter_id", "started_at", "patient_id", "updated_at",
    )
    resources = [r for r in (condition_to_fhir(dict(zip(columns, row))) for row in rows) if r is not None]
    return _fhir_response(_bundle("searchset", resources))


@router.get("/AllergyIntolerance")
def search_fhir_allergies(patient: int | None = None, staff: dict = Depends(get_current_staff)):
    if patient is None:
        return _fhir_response(_bundle("searchset", []))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pa.id, pa.patient_id, pa.allergen, pa.reaction, pa.severity,
                       pa.active, pa.recorded_at
                FROM patient_allergies pa
                JOIN patients p ON p.id = pa.patient_id
                WHERE pa.patient_id = %s AND p.hospital_id = %s
                ORDER BY pa.recorded_at DESC
                LIMIT %s
                """,
                (patient, staff["hospital_id"], _SEARCH_LIMIT),
            )
            rows = cur.fetchall()

    columns = ("id", "patient_id", "allergen", "reaction", "severity", "active", "recorded_at")
    resources = [allergy_to_fhir(dict(zip(columns, row))) for row in rows]
    return _fhir_response(_bundle("searchset", resources))


@router.get("/MedicationRequest")
def search_fhir_medication_requests(patient: int | None = None, staff: dict = Depends(get_current_staff)):
    if patient is None:
        return _fhir_response(_bundle("searchset", []))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pi.id, pi.medicine_name, pi.generic_name, pi.dosage, pi.route,
                       pi.frequency, pi.duration, pi.food_instructions, pi.special_instructions,
                       pi.quantity, pi.quantity_dispensed, pi.medication_id,
                       p.status, p.encounter_id, p.doctor_id, e.patient_id, pi.updated_at
                FROM prescription_items pi
                JOIN prescriptions p ON p.id = pi.prescription_id
                JOIN encounters e ON e.id = p.encounter_id
                WHERE e.patient_id = %s AND e.hospital_id = %s
                ORDER BY pi.updated_at DESC
                LIMIT %s
                """,
                (patient, staff["hospital_id"], _SEARCH_LIMIT),
            )
            rows = cur.fetchall()

    columns = (
        "id", "medicine_name", "generic_name", "dosage", "route", "frequency", "duration",
        "food_instructions", "special_instructions", "quantity", "quantity_dispensed", "medication_id",
        "prescription_status", "encounter_id", "doctor_id", "patient_id", "updated_at",
    )
    resources = [medication_request_to_fhir(dict(zip(columns, row))) for row in rows]
    return _fhir_response(_bundle("searchset", resources))


def _order_result_observations_for_patient(cur, patient_id: int, hospital_id: int, limit: int) -> list[dict]:
    cur.execute(
        """
        SELECT r.id, r.parameter, r.result_value, r.unit, r.unit_system, r.unit_code,
               r.reference_range, r.is_abnormal, r.is_critical, r.recorded_at,
               o.encounter_id, e.patient_id
        FROM order_results r
        JOIN orders o ON o.id = r.order_id
        JOIN encounters e ON e.id = o.encounter_id
        WHERE e.patient_id = %s AND e.hospital_id = %s
        ORDER BY r.recorded_at DESC
        LIMIT %s
        """,
        (patient_id, hospital_id, limit),
    )
    columns = (
        "id", "parameter", "result_value", "unit", "unit_system", "unit_code",
        "reference_range", "is_abnormal", "is_critical", "recorded_at", "encounter_id", "patient_id",
    )
    return [observation_from_order_result_to_fhir(dict(zip(columns, row))) for row in cur.fetchall()]


def _vitals_observations_for_patient(cur, patient_id: int, hospital_id: int, limit: int) -> list[dict]:
    cur.execute(
        """
        SELECT v.id, v.encounter_id, e.patient_id, v.recorded_at,
               v.bp_systolic, v.bp_diastolic, v.pulse, v.temperature_celsius, v.spo2,
               v.respiratory_rate, v.weight_kg, v.height_cm, v.bmi, v.pain_score
        FROM vitals v
        JOIN encounters e ON e.id = v.encounter_id
        WHERE e.patient_id = %s AND e.hospital_id = %s
        ORDER BY v.recorded_at DESC
        LIMIT %s
        """,
        (patient_id, hospital_id, limit),
    )
    columns = (
        "id", "encounter_id", "patient_id", "recorded_at",
        "bp_systolic", "bp_diastolic", "pulse", "temperature_celsius", "spo2",
        "respiratory_rate", "weight_kg", "height_cm", "bmi", "pain_score",
    )
    return [
        r
        for r in (observation_from_vitals_to_fhir(dict(zip(columns, row))) for row in cur.fetchall())
        if r is not None
    ]


@router.get("/Observation")
def search_fhir_observations(patient: int | None = None, staff: dict = Depends(get_current_staff)):
    if patient is None:
        return _fhir_response(_bundle("searchset", []))

    with get_connection() as conn:
        with conn.cursor() as cur:
            resources = _order_result_observations_for_patient(
                cur, patient, staff["hospital_id"], _SEARCH_LIMIT
            ) + _vitals_observations_for_patient(cur, patient, staff["hospital_id"], _SEARCH_LIMIT)

    return _fhir_response(_bundle("searchset", resources))


@router.get("/Patient/{patient_id}/$everything")
def get_fhir_patient_everything(patient_id: int, staff: dict = Depends(get_current_staff)):
    """FHIR's own standard `$everything` operation (not a custom name)
    -- one Bundle containing this patient plus every resource this
    server can produce about them, by reference, not by inlining
    `contained` resources (this repo's own data is never so large that
    a client can't just dereference each entry -- see _SEARCH_LIMIT's
    docstring on real table sizes). Satisfies both Phase 9's Bundle and
    Patient Summary asks with the one real FHIR operation built for
    exactly this."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if not _find_patient(cur, patient_id, staff["hospital_id"]):
                return _not_found("Patient", str(patient_id))

            cur.execute(
                """
                SELECT id, name, whatsapp_number, date_of_birth, gender, uhid,
                       government_id, merged_into_id, email, address_line, city, state, pincode,
                       updated_at
                FROM patients WHERE id = %s AND hospital_id = %s
                """,
                (patient_id, staff["hospital_id"]),
            )
            patient_columns = (
                "id", "name", "whatsapp_number", "date_of_birth", "gender", "uhid",
                "government_id", "merged_into_id", "email", "address_line", "city", "state", "pincode",
                "updated_at",
            )
            resources = [patient_to_fhir(dict(zip(patient_columns, cur.fetchone())))]

            cur.execute(
                """
                SELECT a.id, a.status, a.start_at, a.end_at, a.patient_id, a.doctor_id, t.name,
                       a.updated_at
                FROM appointments a
                JOIN appointment_types t ON t.id = a.appointment_type_id
                WHERE a.patient_id = %s AND a.hospital_id = %s
                ORDER BY a.start_at DESC LIMIT %s
                """,
                (patient_id, staff["hospital_id"], _SEARCH_LIMIT),
            )
            columns = (
                "id", "status", "start_at", "end_at", "patient_id", "doctor_id",
                "appointment_type_name", "updated_at",
            )
            resources += [appointment_to_fhir(dict(zip(columns, row))) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT e.id, e.status, e.encounter_type, e.patient_id, e.doctor_id,
                       e.started_at, e.closed_at, a.id, e.updated_at
                FROM encounters e
                LEFT JOIN appointments a ON a.encounter_id = e.id
                WHERE e.patient_id = %s AND e.hospital_id = %s
                ORDER BY e.started_at DESC LIMIT %s
                """,
                (patient_id, staff["hospital_id"], _SEARCH_LIMIT),
            )
            columns = (
                "id", "status", "encounter_type", "patient_id", "doctor_id", "started_at", "closed_at",
                "appointment_id", "updated_at",
            )
            resources += [encounter_to_fhir(dict(zip(columns, row))) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT c.id, c.diagnosis, c.diagnosis_code_system, c.diagnosis_code,
                       c.diagnosis_code_display, c.encounter_id, c.started_at, e.patient_id,
                       c.updated_at
                FROM consultations c
                JOIN encounters e ON e.id = c.encounter_id
                WHERE e.patient_id = %s AND e.hospital_id = %s
                ORDER BY c.started_at DESC LIMIT %s
                """,
                (patient_id, staff["hospital_id"], _SEARCH_LIMIT),
            )
            columns = (
                "id", "diagnosis", "diagnosis_code_system", "diagnosis_code",
                "diagnosis_code_display", "encounter_id", "started_at", "patient_id", "updated_at",
            )
            resources += [
                r for r in (condition_to_fhir(dict(zip(columns, row))) for row in cur.fetchall()) if r is not None
            ]

            cur.execute(
                """
                SELECT pa.id, pa.patient_id, pa.allergen, pa.reaction, pa.severity,
                       pa.active, pa.recorded_at
                FROM patient_allergies pa
                WHERE pa.patient_id = %s
                ORDER BY pa.recorded_at DESC LIMIT %s
                """,
                (patient_id, _SEARCH_LIMIT),
            )
            columns = ("id", "patient_id", "allergen", "reaction", "severity", "active", "recorded_at")
            resources += [allergy_to_fhir(dict(zip(columns, row))) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT pi.id, pi.medicine_name, pi.generic_name, pi.dosage, pi.route,
                       pi.frequency, pi.duration, pi.food_instructions, pi.special_instructions,
                       pi.quantity, pi.quantity_dispensed, pi.medication_id,
                       p.status, p.encounter_id, p.doctor_id, e.patient_id, pi.updated_at
                FROM prescription_items pi
                JOIN prescriptions p ON p.id = pi.prescription_id
                JOIN encounters e ON e.id = p.encounter_id
                WHERE e.patient_id = %s AND e.hospital_id = %s
                ORDER BY pi.updated_at DESC LIMIT %s
                """,
                (patient_id, staff["hospital_id"], _SEARCH_LIMIT),
            )
            columns = (
                "id", "medicine_name", "generic_name", "dosage", "route", "frequency", "duration",
                "food_instructions", "special_instructions", "quantity", "quantity_dispensed", "medication_id",
                "prescription_status", "encounter_id", "doctor_id", "patient_id", "updated_at",
            )
            resources += [medication_request_to_fhir(dict(zip(columns, row))) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT o.id, o.order_type, o.description, o.priority, o.status,
                       o.ordering_doctor_id, o.ordered_at, o.encounter_id, e.patient_id, o.updated_at
                FROM orders o
                JOIN encounters e ON e.id = o.encounter_id
                WHERE e.patient_id = %s AND e.hospital_id = %s
                ORDER BY o.ordered_at DESC LIMIT %s
                """,
                (patient_id, staff["hospital_id"], _SEARCH_LIMIT),
            )
            columns = (
                "id", "order_type", "description", "priority", "status",
                "ordering_doctor_id", "ordered_at", "encounter_id", "patient_id", "updated_at",
            )
            resources += [service_request_to_fhir(dict(zip(columns, row))) for row in cur.fetchall()]

            resources += _order_result_observations_for_patient(cur, patient_id, staff["hospital_id"], _SEARCH_LIMIT)
            resources += _vitals_observations_for_patient(cur, patient_id, staff["hospital_id"], _SEARCH_LIMIT)

    return _fhir_response(_bundle("collection", resources))
