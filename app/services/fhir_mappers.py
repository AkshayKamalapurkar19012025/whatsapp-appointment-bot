"""
OPD/HIMS interoperability master prompt Phase 8 (FHIR Foundation):
pure mapping functions from this application's own internal domain
rows to FHIR R4 resource dicts. See docs/architecture/FHIR_FOUNDATION.md
for the full mapping rationale, what's deliberately left absent, and
why -- this module's job is narrow: given data the caller (app/api/
fhir.py) already fetched and tenant-checked, build the FHIR JSON shape
for it. No database access happens here, and no resource is ever
partially fabricated to "fill in" a FHIR field this schema doesn't
actually have data for -- an absent internal value stays absent in the
FHIR output (a missing key, not a guessed one).

These functions never assign a code (SNOMED/ICD/LOINC/UCUM/RxNorm) --
every coded field either passes through a value this application's
earlier phases already let a human enter (consultations.diagnosis_code*,
order_results.unit_code*) or is a structural/administrative translation
this module owns directly (e.g. encounter_type 'OPD' -> the FHIR-
standard v3-ActCode 'AMB'), never a clinical judgment about what a
patient's condition or a result's meaning actually is.

FHIR resource `id` is this application's own internal integer primary
key, stringified -- same exposure level (behind the same staff-session
auth + hospital_id tenant check) every existing endpoint in app/api/
already uses for patient_id/appointment_id/etc. in URLs; FHIR
introduces no new identifier scheme. The internal UHID (the actual
*clinical* identity per ADR-001) is carried separately, in
Patient.identifier, not as the resource id.
"""

from typing import Any


def _ref(resource_type: str, internal_id: int | None) -> dict | None:
    if internal_id is None:
        return None
    return {"reference": f"{resource_type}/{internal_id}"}


def _codeable_text(text: str | None) -> dict | None:
    if not text:
        return None
    return {"text": text}


def _clean(resource: dict) -> dict:
    """Drop keys whose value is None/empty -- FHIR JSON never includes
    an element it has nothing to say, rather than a null placeholder."""
    return {k: v for k, v in resource.items() if v not in (None, [], {})}


# ---------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------

_GENDER_MAP = {"MALE": "male", "FEMALE": "female", "OTHER": "other"}


def patient_to_fhir(patient: dict) -> dict:
    """`patient` is a row from `patients` (app/api/patients.py's own
    column set). merged_into_id (migrations/0030_patient_merge.sql)
    maps to Patient.active = false -- a merged-away record is no longer
    the patient's live record, which is exactly what FHIR's own
    Patient.active describes. Patient.link (pointing at the surviving
    record) is not implemented -- see docs/architecture/
    FHIR_FOUNDATION.md's limitations list."""
    identifiers = [
        {
            "system": "urn:whatsapp-appointment-bot:uhid",
            "value": patient["uhid"],
        }
    ]
    if patient.get("government_id"):
        identifiers.append(
            {"system": "urn:whatsapp-appointment-bot:government-id", "value": patient["government_id"]}
        )

    telecom = []
    if patient.get("whatsapp_number"):
        telecom.append({"system": "phone", "value": patient["whatsapp_number"], "use": "mobile"})
    if patient.get("email"):
        telecom.append({"system": "email", "value": patient["email"]})

    address = None
    if any(patient.get(f) for f in ("address_line", "city", "state", "pincode")):
        address = _clean(
            {
                "line": [patient["address_line"]] if patient.get("address_line") else None,
                "city": patient.get("city"),
                "state": patient.get("state"),
                "postalCode": patient.get("pincode"),
            }
        )

    return _clean(
        {
            "resourceType": "Patient",
            "id": str(patient["id"]),
            "identifier": identifiers,
            "active": patient.get("merged_into_id") is None,
            "name": [{"text": patient["name"]}],
            "gender": _GENDER_MAP.get(patient.get("gender") or ""),
            "birthDate": patient["date_of_birth"].isoformat() if patient.get("date_of_birth") else None,
            "telecom": telecom,
            "address": [address] if address else None,
        }
    )


# ---------------------------------------------------------------------
# Practitioner
# ---------------------------------------------------------------------


def practitioner_to_fhir(doctor: dict) -> dict:
    """`doctor` is a row from `doctors`. No license/registration number
    field exists internally (Phase 1's own gap, re-confirmed, not
    invented here) -- Practitioner.identifier is omitted rather than
    populated with the internal id a second time. `qualifications` is
    free text (no structured degree/institution/year coding), so it
    goes into qualification[].code.text -- CodeableConcept's documented
    free-text fallback, not a fabricated coding. specialization is a
    PractitionerRole-level concept in real FHIR, not Practitioner
    itself -- PractitionerRole is not implemented this phase (see
    docs/architecture/FHIR_FOUNDATION.md), so specialization is not
    mapped here at all rather than force-fit into the wrong resource."""
    qualification = None
    if doctor.get("qualifications"):
        qualification = [{"code": {"text": doctor["qualifications"]}}]

    return _clean(
        {
            "resourceType": "Practitioner",
            "id": str(doctor["id"]),
            "active": doctor["active"],
            "name": [{"text": doctor["name"]}],
            "qualification": qualification,
        }
    )


# ---------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------


def organization_to_fhir(hospital: dict) -> dict:
    """`hospital` is a row from `hospitals` -- one row today (this
    deployment is single-tenant, migrations/0027_hospital_tenant_
    context.sql). No address/contact columns exist on `hospitals`, so
    Organization.address/telecom are simply absent, not guessed."""
    return _clean(
        {
            "resourceType": "Organization",
            "id": str(hospital["id"]),
            "identifier": [{"value": hospital["code"]}],
            "name": hospital["name"],
        }
    )


# ---------------------------------------------------------------------
# Appointment
# ---------------------------------------------------------------------

# FHIR Appointment.status: proposed | pending | booked | arrived |
# fulfilled | cancelled | noshow | entered-in-error | checked-in |
# waitlist. REJECTED has no distinct FHIR counterpart -- mapped to
# 'cancelled' (a rejected request never happened, same as this app's
# own booking-overlap logic already treats it, see migrations/
# 0011_appointment_lifecycle_statuses.sql), which is lossy and
# documented, not silently approximated.
_APPOINTMENT_STATUS_MAP = {
    "PENDING": "pending",
    "CONFIRMED": "booked",
    "CHECKED_IN": "checked-in",
    "COMPLETED": "fulfilled",
    "CANCELLED": "cancelled",
    "REJECTED": "cancelled",
}


def appointment_to_fhir(appointment: dict) -> dict:
    """`appointment` is a row from `appointments`, already joined by
    the caller to include `appointment_type_name`. participant.status
    is a required FHIR element with no internal per-participant accept/
    decline concept behind it -- set to 'accepted' for both participants
    as the closest honest default (this application has no notion of a
    doctor/patient declining a specific slot after booking), documented
    as an approximation in docs/architecture/FHIR_FOUNDATION.md."""
    return _clean(
        {
            "resourceType": "Appointment",
            "id": str(appointment["id"]),
            "status": _APPOINTMENT_STATUS_MAP.get(appointment["status"], "proposed"),
            "appointmentType": _codeable_text(appointment.get("appointment_type_name")),
            "start": appointment["start_at"].isoformat(),
            "end": appointment["end_at"].isoformat(),
            "participant": [
                {"actor": _ref("Patient", appointment["patient_id"]), "status": "accepted"},
                {"actor": _ref("Practitioner", appointment["doctor_id"]), "status": "accepted"},
            ],
        }
    )


# ---------------------------------------------------------------------
# Encounter
# ---------------------------------------------------------------------

_ENCOUNTER_STATUS_MAP = {"OPEN": "in-progress", "CLOSED": "finished"}

# encounter_type is CHECK-constrained to 'OPD' only (migrations/
# 0028_encounters.sql) -- this app has exactly one care setting today.
# AMB (ambulatory) is the standard HL7 v3 ActEncounterCode FHIR's own
# Encounter.class expects for outpatient care; this is a structural/
# administrative translation of an internal enum to its established
# external representation, not a clinical coding decision (contrast
# with a diagnosis code, which asserts something about the patient).
_ENCOUNTER_CLASS_MAP = {
    "OPD": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB", "display": "ambulatory"}
}


def encounter_to_fhir(encounter: dict) -> dict:
    """`encounter` is a row from `encounters`, already joined by the
    caller to include `appointment_id` (the one appointment that opened
    it -- appointments.encounter_id, assumed 1:1 the same way
    app/services/order_services.py's list_worklist_orders_service
    already does elsewhere in this codebase)."""
    return _clean(
        {
            "resourceType": "Encounter",
            "id": str(encounter["id"]),
            "status": _ENCOUNTER_STATUS_MAP.get(encounter["status"], "unknown"),
            "class": _ENCOUNTER_CLASS_MAP.get(encounter["encounter_type"]),
            "subject": _ref("Patient", encounter["patient_id"]),
            "participant": [{"individual": _ref("Practitioner", encounter["doctor_id"])}],
            "appointment": [_ref("Appointment", encounter.get("appointment_id"))]
            if encounter.get("appointment_id")
            else None,
            "period": _clean(
                {
                    "start": encounter["started_at"].isoformat(),
                    "end": encounter["closed_at"].isoformat() if encounter.get("closed_at") else None,
                }
            ),
        }
    )


# ---------------------------------------------------------------------
# Condition (diagnosis)
# ---------------------------------------------------------------------


def condition_to_fhir(consultation: dict) -> dict | None:
    """`consultation` is a row from `consultations`, already joined by
    the caller to include `patient_id`. Returns None when diagnosis is
    NULL (a DRAFT consultation with nothing documented yet) -- there is
    nothing meaningful to represent as a Condition, so no resource is
    emitted rather than an empty one.

    diagnosis_code_system (migrations/0056_consultation_diagnosis_
    coding.sql) is free text (e.g. "ICD-10"), not a real URI -- FHIR's
    own Coding.system technically wants a URI. This passes the stored
    text through as-is rather than inventing a URI for a system this
    application has never actually validated against; see
    docs/architecture/FHIR_FOUNDATION.md's limitations for why this
    Condition.code is not claimed to be strictly FHIR-conformant on
    that one point until a real terminology binding exists.

    clinicalStatus/verificationStatus are omitted entirely -- this
    schema does not track a diagnosis's own resolution state (active/
    resolved) separately from the consultation's own DRAFT/COMPLETED
    documentation status, which is a different concept; inferring one
    from the other would assert clinical meaning this data doesn't
    actually carry."""
    if not consultation.get("diagnosis"):
        return None

    code = {"text": consultation["diagnosis"]}
    if consultation.get("diagnosis_code"):
        code["coding"] = [
            _clean(
                {
                    "system": consultation.get("diagnosis_code_system"),
                    "code": consultation["diagnosis_code"],
                    "display": consultation.get("diagnosis_code_display"),
                }
            )
        ]

    return _clean(
        {
            "resourceType": "Condition",
            "id": str(consultation["id"]),
            "code": code,
            "subject": _ref("Patient", consultation["patient_id"]),
            "encounter": _ref("Encounter", consultation["encounter_id"]),
            "recordedDate": consultation["started_at"].isoformat(),
        }
    )


# ---------------------------------------------------------------------
# AllergyIntolerance
# ---------------------------------------------------------------------

_ALLERGY_CLINICAL_STATUS_MAP = {True: "active", False: "resolved"}
_SEVERITY_MAP = {"MILD": "mild", "MODERATE": "moderate", "SEVERE": "severe"}


def allergy_to_fhir(allergy: dict) -> dict:
    """`allergy` is a row from `patient_allergies`. No allergen_code
    column exists (docs/OPD_HIMS_STANDARDS_READINESS.md S7's P2
    proposal, never built) -- code.text only. recordedDate (not
    onsetDateTime) uses `recorded_at`, since that column is honestly
    "when this was documented," not a separately-tracked clinical onset
    -- conflating the two would misrepresent what this data means.
    recorder/asserter are omitted: `recorded_by` is a staff_id, and
    staff are not Practitioners in this application's own domain model
    (only `doctors` are) -- a nurse or receptionist recording an
    allergy is not a clinician to represent as one."""
    reaction = None
    if allergy.get("reaction"):
        reaction = [
            _clean(
                {
                    "manifestation": [{"text": allergy["reaction"]}],
                    "severity": _SEVERITY_MAP.get(allergy.get("severity") or ""),
                }
            )
        ]

    return _clean(
        {
            "resourceType": "AllergyIntolerance",
            "id": str(allergy["id"]),
            "clinicalStatus": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                        "code": _ALLERGY_CLINICAL_STATUS_MAP[allergy["active"]],
                    }
                ]
            },
            "code": {"text": allergy["allergen"]},
            "patient": _ref("Patient", allergy["patient_id"]),
            "recordedDate": allergy["recorded_at"].isoformat(),
            "reaction": reaction,
        }
    )


# ---------------------------------------------------------------------
# Medication (medication master)
# ---------------------------------------------------------------------


def medication_to_fhir(medication: dict) -> dict:
    """`medication` is a row from `medications` (Phase 5's Medication
    Master), including its own computed `display_name` (app/services/
    medication_services.py). No RxNorm/ingredient coding exists
    (Phase 5's own deliberate scope limit) -- code.text only. `strength`
    is free text (e.g. "500mg"), not a parseable numeric Ratio, so it is
    folded into display_name rather than forced into Medication.
    ingredient[].strength, which real FHIR expects as a structured
    Ratio this data cannot honestly supply."""
    form = {"text": medication["dosage_form"]} if medication.get("dosage_form") else None
    return _clean(
        {
            "resourceType": "Medication",
            "id": str(medication["id"]),
            "code": {"text": medication["display_name"]},
            "status": "active" if medication["active"] else "inactive",
            "form": form,
        }
    )


# ---------------------------------------------------------------------
# MedicationRequest (prescription items)
# ---------------------------------------------------------------------


def medication_request_to_fhir(item: dict) -> dict:
    """`item` is a row from `prescription_items`, already joined by the
    caller to include the parent prescription's `status`, `encounter_id`,
    `doctor_id`, and `patient_id`. Uses medicationReference when
    `medication_id` (Phase 5) links to a Medication Master row, and
    medicationCodeableConcept (free text) otherwise -- exactly the same
    optional-link semantics Phase 5 built into prescribing itself.

    dosageInstruction is text-only (Dosage.text): dosage/frequency/
    duration are free text internally (e.g. "500mg", "1-0-1", "5 days"),
    never structured timing -- parsing "1-0-1" into a FHIR Timing would
    be a guess about clinical intent this module does not make."""
    status = "draft"
    if item["prescription_status"] == "PRESCRIBED":
        status = "completed" if item["quantity_dispensed"] >= item["quantity"] else "active"
    elif item["prescription_status"] == "CANCELLED":
        status = "cancelled"

    if item.get("medication_id"):
        medication_field = ("medicationReference", _ref("Medication", item["medication_id"]))
    else:
        text = item["medicine_name"]
        if item.get("generic_name") and item["generic_name"] != item["medicine_name"]:
            text = f"{text} ({item['generic_name']})"
        medication_field = ("medicationCodeableConcept", {"text": text})

    dosage_parts = [
        p
        for p in (item.get("dosage"), item.get("frequency"), item.get("duration"), item.get("food_instructions"))
        if p
    ]
    dosage_instruction = None
    if dosage_parts or item.get("route"):
        dosage_instruction = [
            _clean(
                {
                    "text": ", ".join(dosage_parts) if dosage_parts else None,
                    "route": {"text": item["route"]} if item.get("route") else None,
                }
            )
        ]

    resource = {
        "resourceType": "MedicationRequest",
        "id": str(item["id"]),
        "status": status,
        "intent": "order",
        "subject": _ref("Patient", item["patient_id"]),
        "encounter": _ref("Encounter", item["encounter_id"]),
        "requester": _ref("Practitioner", item["doctor_id"]),
        "dosageInstruction": dosage_instruction,
        "dispenseRequest": {"quantity": {"value": item["quantity"]}},
        medication_field[0]: medication_field[1],
    }
    return _clean(resource)


# ---------------------------------------------------------------------
# Observation (order_results only -- see docs/architecture/
# FHIR_FOUNDATION.md for why vitals-derived Observations are NOT
# implemented this phase)
# ---------------------------------------------------------------------


def _observation_value(result_value: str, unit: str | None, unit_system: str | None, unit_code: str | None) -> dict:
    """Numeric-with-unit results become a real FHIR Quantity; anything
    else (narrative radiology findings, a non-numeric lab flag) stays a
    plain string. This is a syntactic transformation (does the text
    parse as a number?), not a clinical judgment."""
    try:
        numeric = float(result_value)
    except (TypeError, ValueError):
        return {"valueString": result_value}

    quantity = _clean({"value": numeric, "unit": unit, "system": unit_system, "code": unit_code})
    return {"valueQuantity": quantity} if quantity else {"valueString": result_value}


def observation_from_order_result_to_fhir(result: dict) -> dict:
    """`result` is a row from `order_results`, already joined by the
    caller to include the parent order's `encounter_id` and
    `patient_id`. Status is always 'final' -- a result only exists once
    its order reaches COMPLETED, in the same transaction
    (app/services/order_services.py's record_order_result_service), and
    there is no amendment/correction workflow (migrations/0031's own
    header) that would ever produce 'amended'/'corrected'.

    interpretation is included only when is_abnormal/is_critical is
    actually set -- omitted (not asserted "Normal") otherwise, matching
    how the existing result table/UI itself only ever shows an Abnormal/
    Critical pill and shows nothing at all rather than a "Normal" label
    when neither flag is set (frontend/src/admin/ConsultationWorkspace.
    tsx). Two independent booleans can't express High vs. Low (a real,
    previously-documented gap, docs/OPD_HIMS_STANDARDS_READINESS.md
    S12) -- AA covers "critical" and A covers "abnormal" without
    claiming a direction this data doesn't carry."""
    interpretation = None
    if result.get("is_critical"):
        interpretation = [{"coding": [{"code": "AA", "display": "Critical abnormal"}]}]
    elif result.get("is_abnormal"):
        interpretation = [{"coding": [{"code": "A", "display": "Abnormal"}]}]

    return _clean(
        {
            "resourceType": "Observation",
            "id": str(result["id"]),
            "status": "final",
            "code": {"text": result["parameter"]},
            "subject": _ref("Patient", result["patient_id"]),
            "encounter": _ref("Encounter", result["encounter_id"]),
            "effectiveDateTime": result["recorded_at"].isoformat(),
            "referenceRange": [{"text": result["reference_range"]}] if result.get("reference_range") else None,
            "interpretation": interpretation,
            **_observation_value(
                result["result_value"], result.get("unit"), result.get("unit_system"), result.get("unit_code")
            ),
        }
    )


# ---------------------------------------------------------------------
# ServiceRequest (orders)
# ---------------------------------------------------------------------

_SERVICE_REQUEST_STATUS_MAP = {
    "ORDERED": "active",
    "IN_PROGRESS": "active",
    "COMPLETED": "completed",
    "CANCELLED": "revoked",
}
_PRIORITY_MAP = {"ROUTINE": "routine", "URGENT": "urgent", "STAT": "stat"}


def service_request_to_fhir(order: dict) -> dict:
    """`order` is a row from `orders`, already joined by the caller to
    include `patient_id`. category carries order_type (LAB/RADIOLOGY/
    PROCEDURE/SERVICE/EXTERNAL_REFERRAL) as free text, not a coded
    category system -- no such system is chosen internally. authoredOn
    (not occurrenceDateTime) uses `ordered_at`, since this schema
    doesn't separately track a planned/scheduled service time."""
    return _clean(
        {
            "resourceType": "ServiceRequest",
            "id": str(order["id"]),
            "status": _SERVICE_REQUEST_STATUS_MAP.get(order["status"], "unknown"),
            "intent": "order",
            "priority": _PRIORITY_MAP.get(order["priority"]),
            "category": [_codeable_text(order["order_type"])],
            "code": {"text": order["description"]},
            "subject": _ref("Patient", order["patient_id"]),
            "encounter": _ref("Encounter", order["encounter_id"]),
            "requester": _ref("Practitioner", order["ordering_doctor_id"]),
            "authoredOn": order["ordered_at"].isoformat(),
        }
    )
