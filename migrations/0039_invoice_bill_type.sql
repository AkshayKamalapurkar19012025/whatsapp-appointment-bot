-- OPD/HIMS master spec Phase 12: insurance/TPA extension point
-- (section 40).
--
-- Section 40, in full: "The initial OPD version should at least be
-- architecturally ready for: Cash / Self-pay / Corporate / Insurance /
-- TPA / Government scheme" -- that six-way payer-category list is
-- what this migration adds. Its own next paragraph lists a second,
-- separate set of fields -- Payer / Policy / Membership /
-- Authorization / Pre-auth / Co-pay / Patient responsibility / Claim
-- -- under the explicit heading "Future fields", immediately followed
-- by "Do not build a fake insurance system now. Create the correct
-- extension point." Read together: the six-way classification is the
-- extension point to build now; the payer/policy/claim fields are
-- what the spec itself is deferring, not this migration overlooking
-- them. migrations/0033's own header made the same call already --
-- "policy number, pre-auth, co-pay, and claim tracking are real
-- future work, not simulated with empty columns here" -- this
-- migration doesn't revisit that; it only adds the one field that
-- passes that bar: a real, immediately-usable classification a front
-- desk sets when opening a bill, not a form field nothing reads yet.
--
-- payments.method already includes INSURANCE (migrations/0033) -- how
-- a specific payment was actually settled. bill_type is a different,
-- invoice-level fact set once per visit: who this bill is being
-- raised against, independent of which payment methods eventually
-- settle it (a TPA-billed patient can still make a cash co-payment).
ALTER TABLE invoices ADD COLUMN bill_type TEXT NOT NULL DEFAULT 'CASH'
    CHECK (bill_type IN ('CASH', 'SELF_PAY', 'CORPORATE', 'INSURANCE', 'TPA', 'GOVERNMENT_SCHEME'));

COMMENT ON COLUMN invoices.bill_type IS
    'Payer category (master spec section 40) -- staff-set via PATCH /appointments/{id}/bill, same as discount_amount/tax_rate. Defaults CASH, this codebase''s only payer type before this phase.';
