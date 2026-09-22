-- OPD/HIMS master spec section 15's staff notification center (gap #5
-- from docs/OPD_HIMS_MASTER_SPEC_AUDIT.md's summary verdict, second
-- half -- the global search half is app/services/search_service.py,
-- no schema of its own). "No notification/alert bell or center exists.
-- The closest thing is Phase 11's 'Needs Attention' exceptions widget
-- ... which ... only covers the six exception types it defines -- not
-- the broader 'new patient arrived / lab result available /
-- prescription ready' event stream section 15 describes."
--
-- Deliberately a separate table from mock_sms_outbox
-- (migrations/0002) -- that's the PATIENT-facing WhatsApp/SMS mock
-- channel (send_mock_notification, app/services/notifications.py);
-- this is a STAFF-facing, in-app feed, a different audience and a
-- different read/unread lifecycle (a patient message is fire-and-
-- forget, this is a bell icon staff actively clear).
--
-- Hospital-wide, not per-staff or per-role: the three event kinds this
-- phase actually emits (arrival/lab-result/prescription-ready) are
-- all front-desk-and-clinical-team-relevant, and every staff account
-- already effectively sees the same operational data (dashboard/
-- exceptions/queue aren't role-partitioned either) -- per-recipient
-- targeting would need the role infrastructure migrations/0043 only
-- just made real, so it's a scoped follow-up, not built here.
CREATE TABLE notifications (
    id             BIGSERIAL PRIMARY KEY,
    hospital_id    BIGINT NOT NULL REFERENCES hospitals(id),
    kind           TEXT NOT NULL
        CHECK (kind IN ('PATIENT_ARRIVED', 'LAB_RESULT_AVAILABLE', 'PRESCRIPTION_READY')),
    message        TEXT NOT NULL,
    appointment_id BIGINT REFERENCES appointments(id),
    read_at        TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial index on the actual hot-path query (unread, newest first,
-- one hospital) -- mirrors patient_allergies_patient_id_idx's same
-- "partial index on the filtered subset a query actually asks for"
-- pattern (migrations/0042).
CREATE INDEX notifications_hospital_unread_idx
    ON notifications (hospital_id, created_at DESC)
    WHERE read_at IS NULL;

COMMENT ON TABLE notifications IS
    'Staff-facing in-app notification center (master spec section 15) -- hospital-wide, read/unread, fed by check-in/lab-result/prescription-ready events.';
