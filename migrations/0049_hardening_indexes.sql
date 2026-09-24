-- Phase 12 (Production Hardening) indexing pass -- master spec audit
-- gap #7. Evidenced against real query call sites (not a speculative
-- add-everything pass): every index below has at least one confirmed
-- WHERE/JOIN filtering on that exact column with no index available
-- to it today (checked via `\d` against the live schema, not just the
-- migration files -- a NOT NULL UNIQUE column, for example, already
-- gets an implicit index from the constraint and would have been a
-- false positive here).
--
-- encounters had zero secondary indexes at all before this migration
-- (only its own primary key) despite being the join spine almost
-- every clinical/billing/timeline query hangs off:
--   patient_id -- app/services/patient_timeline_service.py's Patient
--                 360 query (WHERE e.patient_id = %s, a frequently
--                 clicked screen) and app/services/patient_merge.py
--                 (two separate WHERE patient_id = %s queries).
--   hospital_id -- app/services/billing_history_service.py (both
--                  list_invoices_service and list_payments_service)
--                  and app/services/waiting_time_analytics_service.py
--                  -- three separate WHERE e.hospital_id = %s call
--                  sites, none of them index-backed.
CREATE INDEX encounters_patient_id_idx ON encounters (patient_id);
CREATE INDEX encounters_hospital_id_idx ON encounters (hospital_id);

-- appointments already has two well-targeted partial indexes
-- (doctor_id/patient_id scoped to live statuses) but nothing on
-- hospital_id, which app/services/search_service.py's global-search
-- appointment branch filters by directly (WHERE a.hospital_id = %s).
CREATE INDEX appointments_hospital_id_idx ON appointments (hospital_id);
