"""
M7 (in progress, HospitalOS build plan): insert_patient() and verify_otp()
no longer use ON CONFLICT (whatsapp_number) DO NOTHING -- it becomes
invalid SQL the moment patients.whatsapp_number's UNIQUE constraint is
actually dropped (ON CONFLICT needs a matching constraint/index to
target). Until that drop ships, the constraint is still live, so the
same race those clauses used to resolve gracefully can still raise
psycopg.errors.UniqueViolation -- both functions now catch it and roll
back explicitly, the same "an aborted transaction needs an explicit
ROLLBACK before the caller can touch the connection again" lesson
already applied to SlotOverlap (see app/services/appointment_services.py
and its own tests).

Forced deterministically here (no thread race needed): a UNIQUE
constraint check sees uncommitted rows from earlier in the same
transaction, so calling insert_patient() twice with the same number on
the same cursor hits it on the second call every time.
"""

from app.api.patients import insert_patient


def test_insert_patient_returns_none_on_conflict_and_leaves_cursor_usable(db_connection):
    """
    Two separate transactions, like the real call sites (each HTTP
    request opens its own connection/transaction via get_connection())
    -- not two calls on one shared, uncommitted transaction, which would
    make the second call's rollback wrongly undo the first call's
    already-legitimate insert too (a real thing this test's first draft
    got wrong and caught before it shipped).
    """
    number = "+919750000002"

    with db_connection.cursor() as cur:
        first = insert_patient(cur, "Race Patient A", number)
        assert first is not None
    db_connection.commit()

    with db_connection.cursor() as cur:
        second = insert_patient(cur, "Race Patient B", number)
        assert second is None, "a duplicate number must not create a second patient"

        # Proves recovery: an unrelated query on the same cursor must
        # succeed, not raise psycopg.errors.InFailedSqlTransaction --
        # the exact failure this test would show if the UniqueViolation
        # catch's rollback were missing.
        cur.execute("SELECT count(*) FROM patients WHERE whatsapp_number = %s", (number,))
        assert cur.fetchone()[0] == 1
    db_connection.commit()
