-- OPD/HIMS master spec Phase 14: controlled amendment for completed
-- consultations (section 70: "Clinical/financial records should have
-- controlled amendment/void processes"). Financial records already
-- have their controlled void (charges/payments/invoices, migrations/
-- 0033: VOIDED status, a required reason, the row itself never
-- deleted). Clinical records had the opposite: a consultation simply
-- refuses any edit once COMPLETED
-- (app/services/clinical_services.py's own module-level note: "no
-- amendment workflow yet"). This closes that gap the same way the
-- financial side already works -- never silently overwrite the record
-- of what was actually documented: archive the pre-amendment snapshot,
-- require a reason, attribute who and when.
--
-- The CURRENT values keep living on consultations itself (updated in
-- place by the amendment, same row patient-facing reads already query)
-- -- this table holds only the superseded snapshot, one row per
-- amendment, so "what did this consultation say before correction N"
-- is always answerable without reconstructing it from a diff.
CREATE TABLE consultation_amendments (
    id                          BIGSERIAL PRIMARY KEY,
    consultation_id             BIGINT NOT NULL REFERENCES consultations(id),
    previous_chief_complaint    TEXT,
    previous_history_notes      TEXT,
    previous_examination_notes  TEXT,
    previous_diagnosis          TEXT,
    previous_clinical_notes     TEXT,
    previous_follow_up_date     DATE,
    previous_follow_up_reason   TEXT,
    reason                      TEXT NOT NULL,
    amended_by                  BIGINT NOT NULL REFERENCES staff(id),
    amended_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX consultation_amendments_consultation_id_idx ON consultation_amendments (consultation_id);

COMMENT ON TABLE consultation_amendments IS
    'One row per correction made to a COMPLETED consultation -- the PRE-amendment snapshot, never the current values. Never updated or deleted once written: the audit trail for what a consultation used to say before it was corrected. See app/services/clinical_services.py''s amend_consultation_service.';

-- consultation.amend is deliberately its own permission, not folded
-- into an existing one -- correcting a signed-off clinical record
-- after the fact is a materially different, more sensitive action than
-- writing one for the first time (clinical.* has no manage-tier
-- permission at all today; every consultation write up to this phase
-- was gated on CHECKED_IN, not RBAC).
INSERT INTO permissions (name) VALUES ('consultation.amend');

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions WHERE name = 'consultation.amend';
