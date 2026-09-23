-- OPD/HIMS master spec audit "subsequent gaps" list (section 44-47's
-- write-up area): "'Admit to IPD' disposition scaffold does not
-- exist. No disposition field, no Follow-up/Refer/Admit-to-IPD/
-- Emergency choice in the consultation workspace -- this was never
-- built even as a stub, unlike External Referral which was."
--
-- A stub, same as External Referral (orders.order_type =
-- 'EXTERNAL_REFERRAL', migrations/0030_orders.sql): a captured choice
-- on the consultation record, not a real IPD admission workflow --
-- there is no IPD module to admit into yet (see this same audit's
-- section 44-47 note on encounters.encounter_type still being
-- OPD-only). FOLLOW_UP overlaps with the existing follow_up_date/
-- follow_up_reason fields, but is included anyway: the spec names it
-- as one of the four disposition choices, and a doctor picking it here
-- is recording *at completion time* what happens next, distinct from
-- those two fields (which can be set without ever touching
-- disposition, and vice versa).
ALTER TABLE consultations
    ADD COLUMN disposition TEXT
        CHECK (disposition IN ('FOLLOW_UP', 'REFER', 'ADMIT_TO_IPD', 'EMERGENCY')),
    ADD COLUMN disposition_notes TEXT;

COMMENT ON COLUMN consultations.disposition IS
    'Optional. One of the four disposition choices the master spec names (section 44-47) -- a stub, same category as External Referral: captures the choice, does not itself trigger any IPD/referral workflow.';
COMMENT ON COLUMN consultations.disposition_notes IS
    'Optional free text alongside disposition -- e.g. which specialist for REFER, or the reason for ADMIT_TO_IPD/EMERGENCY.';

-- Same archive-then-update shape migrations/0041 already established
-- for every other consultation field an amendment can change.
ALTER TABLE consultation_amendments
    ADD COLUMN previous_disposition TEXT,
    ADD COLUMN previous_disposition_notes TEXT;
