"""
M8 (HospitalOS build plan): duplicate detection at registration. Warns,
never blocks -- a match here never prevents the new patient from being
created.

Scoped to app/api/patients.py's admin create_patient only, not the
WhatsApp/web self-registration paths (app/api/scheduling.py,
app/services/patient_auth.py's verify_otp): this work order's own
"records the reviewer's decision either way" assumes a staff reviewer
in the loop, which only exists on the admin path. A PENDING row nobody
can ever act on isn't a warning, it's just a leak -- if self-service
detection is wanted later, it needs its own decision, not a silent
default answered here.
"""

from app.services.exceptions import DuplicateReviewNotFound

NAME_SIMILARITY_THRESHOLD = 0.35

REVIEWER_DECISIONS = ("CONFIRMED_DUPLICATE", "NOT_DUPLICATE")


def find_duplicate_candidates(cur, hospital_id: int, new_patient_id: int) -> list[dict]:
    """
    Looks for existing patients (excluding new_patient_id itself, and
    never a patient already retired by an earlier merge) that might be
    the same person as the newly registered one, matching on any of:
    name similarity (pg_trgm's similarity(), NAME_SIMILARITY_THRESHOLD
    is a starting point -- per this work order's own note, the
    reviewer decisions recorded below are the training data for tuning
    it later, once real registration data exists to tune against), same
    date_of_birth, a shared identifier (see
    app/services/patient_identifiers.py -- covers phone today, GOVT_ID
    once that's ever written), or the same government_id.

    Records one PENDING patient_duplicate_reviews row per candidate and
    returns the same list, for the caller to surface as a warning.
    """
    cur.execute(
        "SELECT name, date_of_birth, government_id FROM patients WHERE id = %s",
        (new_patient_id,),
    )
    new_patient = cur.fetchone()

    if new_patient is None:
        return []

    name, date_of_birth, government_id = new_patient

    cur.execute(
        """
        SELECT
            p.id,
            p.name,
            similarity(p.name, %(name)s::text) AS name_similarity,
            (%(dob)s::date IS NOT NULL AND p.date_of_birth = %(dob)s::date) AS dob_match,
            (%(govt_id)s::text IS NOT NULL AND p.government_id = %(govt_id)s::text) AS govt_id_match,
            EXISTS (
                SELECT 1
                FROM patient_identifiers pi_new
                JOIN patient_identifiers pi_existing
                    ON pi_existing.kind = pi_new.kind AND pi_existing.value = pi_new.value
                WHERE pi_new.patient_id = %(new_patient_id)s::bigint
                  AND pi_existing.patient_id = p.id
            ) AS shared_identifier
        FROM patients p
        WHERE p.hospital_id = %(hospital_id)s::bigint
          AND p.id <> %(new_patient_id)s::bigint
          AND p.merged_into_id IS NULL
          AND (
              similarity(p.name, %(name)s::text) >= %(threshold)s::real
              OR (%(dob)s::date IS NOT NULL AND p.date_of_birth = %(dob)s::date)
              OR (%(govt_id)s::text IS NOT NULL AND p.government_id = %(govt_id)s::text)
              OR EXISTS (
                  SELECT 1
                  FROM patient_identifiers pi_new
                  JOIN patient_identifiers pi_existing
                      ON pi_existing.kind = pi_new.kind AND pi_existing.value = pi_new.value
                  WHERE pi_new.patient_id = %(new_patient_id)s::bigint
                    AND pi_existing.patient_id = p.id
              )
          )
        """,
        {
            "name": name,
            "dob": date_of_birth,
            "govt_id": government_id,
            "new_patient_id": new_patient_id,
            "hospital_id": hospital_id,
            "threshold": NAME_SIMILARITY_THRESHOLD,
        },
    )
    candidates = cur.fetchall()

    results = []

    for candidate_id, candidate_name, name_sim, dob_match, govt_match, shared_identifier in candidates:
        matched_on = []
        if name_sim is not None and name_sim >= NAME_SIMILARITY_THRESHOLD:
            matched_on.append("name")
        if dob_match:
            matched_on.append("date_of_birth")
        if shared_identifier:
            matched_on.append("phone")
        if govt_match:
            matched_on.append("government_id")

        cur.execute(
            """
            INSERT INTO patient_duplicate_reviews (
                hospital_id, new_patient_id, candidate_patient_id, matched_on, name_similarity
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (hospital_id, new_patient_id, candidate_id, matched_on, name_sim),
        )
        review_id = cur.fetchone()[0]

        results.append(
            {
                "review_id": review_id,
                "candidate_patient_id": candidate_id,
                "candidate_name": candidate_name,
                "matched_on": matched_on,
                "name_similarity": name_sim,
            }
        )

    return results


def decide_duplicate_review(cur, review_id: int, decision: str, staff_id: int) -> None:
    """
    Records a reviewer's decision on a pending duplicate candidate --
    CONFIRMED_DUPLICATE or NOT_DUPLICATE, per this work order's own note
    that these decisions are the training data for tuning
    NAME_SIMILARITY_THRESHOLD later. Recorded either way, never
    overwritten: deciding an already-decided review raises
    DuplicateReviewNotFound, same as an unknown id -- from the caller's
    side these look identical (nothing to decide).

    Deliberately does not act on CONFIRMED_DUPLICATE by merging
    anything automatically -- confirming a match is a signal a human
    made the call, not an instruction to execute merge_patients(),
    which a reviewer calls separately once they've decided that's the
    right outcome (it might not be: two real people can still share a
    name and birthday).
    """
    if decision not in REVIEWER_DECISIONS:
        raise ValueError(f"decision must be one of {REVIEWER_DECISIONS}, got {decision!r}")

    cur.execute(
        """
        UPDATE patient_duplicate_reviews
        SET reviewer_decision = %s,
            reviewed_by_staff_id = %s,
            decided_at = NOW()
        WHERE id = %s
          AND reviewer_decision = 'PENDING'
        RETURNING id
        """,
        (decision, staff_id, review_id),
    )

    if cur.fetchone() is None:
        raise DuplicateReviewNotFound()
