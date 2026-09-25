"""
P0 clinical safety (OPD/HIMS interoperability master prompt Phase 4):
warn a clinician when a medicine being prescribed textually matches one
of the patient's recorded allergies. See docs/OPD_HIMS_STANDARDS_
READINESS.md S17 for the approved design this implements, and docs/
workflows/PHARMACY.md for the documented matching limitation.

Matching strategy (deliberately simple, justified by the data that
actually exists -- see docs/OPD_HIMS_TERMINOLOGY_AUDIT.md): both
patient_allergies.allergen and prescription_items.medicine_name/
generic_name are free text (migrations/0042, migrations/0032). There is
no terminology code, no drug-class table, and no ingredient relationship
anywhere in this schema (confirmed by three prior audit phases) --
building one is explicitly out of scope here. So this checks the one
thing the data actually supports: does the recorded allergen text appear
as a substring of the medicine's name or generic name, case-insensitively.

What this catches: a patient allergic to "Penicillin" being prescribed
"Penicillin", "Penicillin V", or anything else whose name contains that
text.

What this does NOT catch: clinically related but differently-named
drugs (e.g. a "Penicillin" allergy against a prescription for
"Amoxicillin" -- they're in the same drug class, but nothing in this
match, or in the underlying data, knows that). This is not a bug to
silently work around; it is the honest limit of free-text matching
without a real drug-class/ingredient terminology, which is future work
(docs/OPD_HIMS_STANDARDS_READINESS.md S5/S14), not this phase's.

False positives are possible: a short or common allergen string could
appear inside an unrelated medicine name by coincidence. False negatives
are possible and expected: any allergy/medicine pair that doesn't share
literal text (brand vs. generic naming, misspellings, drug-class
relationships) will not be caught. Both are named explicitly here so
this is never mistaken for real drug-allergy decision support.

Phase 5 (migrations/0054_medication_master.sql) extension: when the
item being added resolves to a Medication Master row, that row's own
generic_name/brand_name are added to the candidate text alongside the
free-text medicine_name/generic_name that were actually typed. This is
still the exact same substring match, not new intelligence -- it just
means an ad hoc "Panadol" entry that resolves to the canonical
Medication "Paracetamol" now also gets checked against "Paracetamol",
even if the clinician never typed that word themselves. It does not
close the drug-class gap above (Penicillin still doesn't match
Amoxicillin); that remains explicitly out of scope.
"""


def check_allergy_conflicts(
    cur,
    patient_id: int,
    medicine_name: str,
    generic_name: str | None,
    *,
    medication: dict | None = None,
) -> list[dict]:
    cur.execute(
        """
        SELECT id, allergen, severity, reaction
        FROM patient_allergies
        WHERE patient_id = %s AND active = TRUE
        """,
        (patient_id,),
    )
    allergies = cur.fetchall()
    if not allergies:
        return []

    candidate_names = [medicine_name, generic_name]
    if medication is not None:
        candidate_names.extend([medication.get("generic_name"), medication.get("brand_name")])
    candidates = [name.strip().lower() for name in candidate_names if name and name.strip()]

    conflicts = []
    for allergy_id, allergen, severity, reaction in allergies:
        allergen_normalized = allergen.strip().lower()
        if not allergen_normalized:
            continue
        matched_against = next((name for name in candidates if allergen_normalized in name), None)
        if matched_against is not None:
            conflicts.append(
                {
                    "allergy_id": allergy_id,
                    "allergen": allergen,
                    "severity": severity,
                    "reaction": reaction,
                    "matched_against": matched_against,
                }
            )
    return conflicts
