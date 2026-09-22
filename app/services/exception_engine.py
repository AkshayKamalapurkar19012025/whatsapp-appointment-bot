"""
Exception engine (OPD/HIMS master spec Phase 11, sections 46-47):
"no alerting/threshold layer exists" (docs/OPD_HIMS_P0_AUDIT.md section
5, and flagged again as "Phase 11 territory" in docs/OPD_HIMS_P8_
PRESCRIPTION_PHARMACY.md's own known-gaps section).

Section 46 is explicit: "Do NOT create hundreds of noisy alerts. Only
create actionable exceptions", and every exception must carry What
happened / Why it matters / Who should act / Recommended action /
Current status. Section 47's example workflow ends with "Exception
resolved" being reached by *taking the action* (open the patient,
understand why, act), not by dismissing a notification.

That shapes the whole design: exceptions here are computed live from
current operational state, not written to a table. Every condition this
detects (a patient still waiting, an order still pending, a bill still
unpaid) is already represented by existing rows -- the "resolution" of
an exception is simply the underlying row changing (vitals get
recorded, the order gets a result, the bill gets paid), at which point
it stops matching the query and disappears from the next call on its
own. Storing a parallel alerts table would mean a second place that can
drift from the truth and a dismiss/resolve workflow the spec doesn't
ask for -- so current_status is always "OPEN": anything this function
returns is, by construction, still true right now.

Six exception types, each a single bulk query (never per-row/per-patient
loops), covering 6 of the master spec's 8 examples:
- WAITING_FOR_TRIAGE / WAITING_FOR_DOCTOR -- split "patient waiting" and
  "checked in but not called" into the two real OPD queue stages
  (before vitals, and after vitals but before the doctor has started)
  rather than one vague "waiting" signal.
- ORDER_PENDING -- covers both "order pending too long" and "lab result
  delayed": the master spec lists them as separate examples, but
  they're the same underlying signal (an order awaiting a result past a
  threshold) differing only by order_type, which the payload reports.
- PRESCRIPTION_NOT_DISPENSED, BILLING_NOT_STARTED, PAYMENT_PENDING map
  directly to their spec examples.

"Doctor running late" is deliberately not implemented: every other type
here is a direct threshold on one row's own timestamp, but "late"
requires comparing a doctor's actual pace against their schedule, which
needs a real running-average/adherence model this phase doesn't build --
guessing at a shallow heuristic would risk exactly the "hundreds of
noisy alerts" section 46 warns against. Left as a known gap.

Thresholds are per-type constants for now, not a per-hospital setting --
this repo is still single-tenant in practice (see hospital_id's own
"purely structural" migration, 0027), so a configuration UI for a second
hospital's different thresholds is speculative until one exists.
"""

# Minutes. STAT/URGENT orders get shorter thresholds than ROUTINE --
# the one place clinical priority (orders.priority, already established
# in migrations/0030) changes how soon a delay becomes actionable.
_WAITING_FOR_TRIAGE_MINUTES = 30
_WAITING_FOR_DOCTOR_MINUTES = 30
_ORDER_PENDING_MINUTES = {"STAT": 30, "URGENT": 60, "ROUTINE": 180}
_PRESCRIPTION_NOT_DISPENSED_MINUTES = 120
_BILLING_NOT_STARTED_MINUTES = 15
_PAYMENT_PENDING_MINUTES = 10


def _waiting_for_triage(cur, hospital_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT a.id, a.patient_id, p.name, a.doctor_id, d.name, a.visited_at,
               EXTRACT(EPOCH FROM (NOW() - a.visited_at)) / 60
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN doctors d ON d.id = a.doctor_id
        WHERE a.hospital_id = %s
          AND a.status = 'CHECKED_IN'
          AND a.encounter_id IS NOT NULL
          AND a.visited_at <= NOW() - (%s || ' minutes')::INTERVAL
          AND NOT EXISTS (SELECT 1 FROM vitals v WHERE v.encounter_id = a.encounter_id)
        ORDER BY a.visited_at
        """,
        (hospital_id, _WAITING_FOR_TRIAGE_MINUTES),
    )
    results = []
    for appt_id, patient_id, patient_name, doctor_id, doctor_name, visited_at, age in cur.fetchall():
        age = int(age)
        results.append(
            {
                "type": "WAITING_FOR_TRIAGE",
                "patient_id": patient_id,
                "patient_name": patient_name,
                "appointment_id": appt_id,
                "doctor_id": doctor_id,
                "doctor_name": doctor_name,
                "detected_at": visited_at.isoformat(),
                "age_minutes": age,
                "what_happened": f"{patient_name} checked in {age} minutes ago and hasn't had vitals recorded yet.",
                "why_it_matters": "The patient is waiting with no one yet aware they need triage.",
                "who_should_act": "Reception / Nursing",
                "recommended_action": "Send the patient to triage for vitals.",
                "current_status": "OPEN",
            }
        )
    return results


def _waiting_for_doctor(cur, hospital_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT a.id, a.patient_id, p.name, a.doctor_id, d.name, vt.recorded_at,
               EXTRACT(EPOCH FROM (NOW() - vt.recorded_at)) / 60
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN doctors d ON d.id = a.doctor_id
        JOIN LATERAL (
            SELECT MAX(v.recorded_at) AS recorded_at
            FROM vitals v
            WHERE v.encounter_id = a.encounter_id
        ) vt ON vt.recorded_at IS NOT NULL
        LEFT JOIN consultations c ON c.encounter_id = a.encounter_id
        WHERE a.hospital_id = %s
          AND a.status = 'CHECKED_IN'
          AND a.encounter_id IS NOT NULL
          AND c.id IS NULL
          AND vt.recorded_at <= NOW() - (%s || ' minutes')::INTERVAL
        ORDER BY vt.recorded_at
        """,
        (hospital_id, _WAITING_FOR_DOCTOR_MINUTES),
    )
    results = []
    for appt_id, patient_id, patient_name, doctor_id, doctor_name, recorded_at, age in cur.fetchall():
        age = int(age)
        results.append(
            {
                "type": "WAITING_FOR_DOCTOR",
                "patient_id": patient_id,
                "patient_name": patient_name,
                "appointment_id": appt_id,
                "doctor_id": doctor_id,
                "doctor_name": doctor_name,
                "detected_at": recorded_at.isoformat(),
                "age_minutes": age,
                "what_happened": f"{patient_name} was triaged {age} minutes ago and Dr. {doctor_name} hasn't started the consultation.",
                "why_it_matters": "The patient has been triaged and is ready, but is still waiting to be seen.",
                "who_should_act": "Doctor / OPD Manager",
                "recommended_action": f"Check on Dr. {doctor_name}'s queue and call the patient in.",
                "current_status": "OPEN",
            }
        )
    return results


def _order_pending(cur, hospital_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT o.id, o.order_type, o.description, o.priority, o.ordered_at,
               e.patient_id, p.name,
               EXTRACT(EPOCH FROM (NOW() - o.ordered_at)) / 60
        FROM orders o
        JOIN encounters e ON e.id = o.encounter_id
        JOIN patients p ON p.id = e.patient_id
        WHERE e.hospital_id = %s
          AND o.status IN ('ORDERED', 'IN_PROGRESS')
          AND o.ordered_at <= NOW() - (CASE o.priority
                  WHEN 'STAT' THEN %s
                  WHEN 'URGENT' THEN %s
                  ELSE %s
              END || ' minutes')::INTERVAL
        ORDER BY o.ordered_at
        """,
        (
            hospital_id,
            _ORDER_PENDING_MINUTES["STAT"],
            _ORDER_PENDING_MINUTES["URGENT"],
            _ORDER_PENDING_MINUTES["ROUTINE"],
        ),
    )
    results = []
    for order_id, order_type, description, priority, ordered_at, patient_id, patient_name, age in cur.fetchall():
        age = int(age)
        results.append(
            {
                "type": "ORDER_PENDING",
                "patient_id": patient_id,
                "patient_name": patient_name,
                "order_id": order_id,
                "order_type": order_type,
                "priority": priority,
                "detected_at": ordered_at.isoformat(),
                "age_minutes": age,
                "what_happened": f"{order_type.title()} order \"{description}\" for {patient_name} ({priority}) has been pending {age} minutes.",
                "why_it_matters": "The result is still outstanding, holding up the consultation or discharge.",
                "who_should_act": "Lab / Radiology / Procedure team",
                "recommended_action": "Follow up on the order and record the result.",
                "current_status": "OPEN",
            }
        )
    return results


def _prescription_not_dispensed(cur, hospital_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT pr.id, pr.prescribed_at, e.patient_id, p.name,
               EXTRACT(EPOCH FROM (NOW() - pr.prescribed_at)) / 60
        FROM prescriptions pr
        JOIN encounters e ON e.id = pr.encounter_id
        JOIN patients p ON p.id = e.patient_id
        WHERE e.hospital_id = %s
          AND pr.status = 'PRESCRIBED'
          AND pr.prescribed_at <= NOW() - (%s || ' minutes')::INTERVAL
          AND EXISTS (
              SELECT 1 FROM prescription_items pi
              WHERE pi.prescription_id = pr.id AND pi.quantity_dispensed < pi.quantity
          )
        ORDER BY pr.prescribed_at
        """,
        (hospital_id, _PRESCRIPTION_NOT_DISPENSED_MINUTES),
    )
    results = []
    for prescription_id, prescribed_at, patient_id, patient_name, age in cur.fetchall():
        age = int(age)
        results.append(
            {
                "type": "PRESCRIPTION_NOT_DISPENSED",
                "patient_id": patient_id,
                "patient_name": patient_name,
                "prescription_id": prescription_id,
                "detected_at": prescribed_at.isoformat(),
                "age_minutes": age,
                "what_happened": f"{patient_name}'s prescription was written {age} minutes ago and hasn't been fully dispensed.",
                "why_it_matters": "The patient may be waiting at the pharmacy, or may leave without their medicine.",
                "who_should_act": "Pharmacy",
                "recommended_action": "Dispense the remaining prescribed items.",
                "current_status": "OPEN",
            }
        )
    return results


def _billing_not_started(cur, hospital_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT c.encounter_id, c.completed_at, e.patient_id, p.name,
               EXTRACT(EPOCH FROM (NOW() - c.completed_at)) / 60
        FROM consultations c
        JOIN encounters e ON e.id = c.encounter_id
        JOIN patients p ON p.id = e.patient_id
        LEFT JOIN invoices inv ON inv.encounter_id = c.encounter_id
        LEFT JOIN charges ch ON ch.invoice_id = inv.id AND ch.status = 'ACTIVE'
        WHERE e.hospital_id = %s
          AND c.status = 'COMPLETED'
          AND c.completed_at <= NOW() - (%s || ' minutes')::INTERVAL
        GROUP BY c.encounter_id, c.completed_at, e.patient_id, p.name
        HAVING COUNT(ch.id) = 0
        ORDER BY c.completed_at
        """,
        (hospital_id, _BILLING_NOT_STARTED_MINUTES),
    )
    results = []
    for encounter_id, completed_at, patient_id, patient_name, age in cur.fetchall():
        age = int(age)
        results.append(
            {
                "type": "BILLING_NOT_STARTED",
                "patient_id": patient_id,
                "patient_name": patient_name,
                "encounter_id": encounter_id,
                "detected_at": completed_at.isoformat(),
                "age_minutes": age,
                "what_happened": f"{patient_name}'s consultation finished {age} minutes ago with no charges billed yet.",
                "why_it_matters": "The visit is clinically done but nothing has been billed for it.",
                "who_should_act": "Billing / Front desk",
                "recommended_action": "Open the visit and raise the bill.",
                "current_status": "OPEN",
            }
        )
    return results


def _payment_pending(cur, hospital_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT inv.encounter_id, e.patient_id, p.name, e.closed_at,
               EXTRACT(EPOCH FROM (NOW() - e.closed_at)) / 60,
               COALESCE(gross.amount, 0) AS gross_amount,
               inv.discount_amount, inv.tax_rate,
               COALESCE(paid.amount, 0) AS paid_amount
        FROM invoices inv
        JOIN encounters e ON e.id = inv.encounter_id
        JOIN patients p ON p.id = e.patient_id
        LEFT JOIN LATERAL (
            SELECT SUM(amount) AS amount FROM charges
            WHERE invoice_id = inv.id AND status = 'ACTIVE'
        ) gross ON TRUE
        LEFT JOIN LATERAL (
            SELECT SUM(amount - refunded_amount) AS amount FROM payments
            WHERE invoice_id = inv.id AND status = 'COMPLETED'
        ) paid ON TRUE
        WHERE e.hospital_id = %s
          AND inv.status = 'OPEN'
          AND e.status = 'CLOSED'
          AND e.closed_at <= NOW() - (%s || ' minutes')::INTERVAL
        """,
        (hospital_id, _PAYMENT_PENDING_MINUTES),
    )
    results = []
    for encounter_id, patient_id, patient_name, closed_at, age, gross, discount, tax_rate, paid in cur.fetchall():
        taxable = max(gross - discount, 0)
        net_amount = taxable + round(taxable * tax_rate / 100, 2)
        balance = net_amount - paid
        if balance <= 0:
            continue
        age = int(age)
        results.append(
            {
                "type": "PAYMENT_PENDING",
                "patient_id": patient_id,
                "patient_name": patient_name,
                "encounter_id": encounter_id,
                "detected_at": closed_at.isoformat(),
                "age_minutes": age,
                "balance": float(balance),
                "what_happened": f"{patient_name}'s visit ended {age} minutes ago with ₹{balance:.2f} still unpaid.",
                "why_it_matters": "Outstanding balance left unsettled after the visit is closed.",
                "who_should_act": "Billing / Front desk",
                "recommended_action": "Collect the remaining payment or follow up with the patient.",
                "current_status": "OPEN",
            }
        )
    return results


def get_active_exceptions_service(cur, *, hospital_id: int) -> dict:
    exceptions = (
        _waiting_for_triage(cur, hospital_id)
        + _waiting_for_doctor(cur, hospital_id)
        + _order_pending(cur, hospital_id)
        + _prescription_not_dispensed(cur, hospital_id)
        + _billing_not_started(cur, hospital_id)
        + _payment_pending(cur, hospital_id)
    )
    exceptions.sort(key=lambda x: x["age_minutes"], reverse=True)
    return {"exceptions": exceptions, "count": len(exceptions)}
