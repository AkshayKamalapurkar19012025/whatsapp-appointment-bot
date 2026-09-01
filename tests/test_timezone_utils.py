"""Unit tests for app/utils/timezone.py -- pure logic, no HTTP involved.

get_doctor_timezone() tests use the db_connection fixture since that one
function necessarily touches the database; everything else here is a
plain function call.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.utils.timezone import (
    validate_timezone,
    get_timezone,
    make_aware_datetime,
    ensure_aware_datetime,
    convert_to_timezone,
    overlaps,
    get_doctor_timezone,
)


def test_validate_timezone_accepts_valid_iana_names():
    assert validate_timezone("Asia/Kolkata") is True
    assert validate_timezone("America/New_York") is True


def test_validate_timezone_rejects_invalid_name():
    assert validate_timezone("Not/AZone") is False


def test_validate_timezone_rejects_empty_or_none():
    assert validate_timezone("") is False
    assert validate_timezone(None) is False


def test_get_timezone_returns_zoneinfo():
    tz = get_timezone("Asia/Kolkata")
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "Asia/Kolkata"


def test_get_timezone_raises_for_invalid_name():
    with pytest.raises(ValueError):
        get_timezone("Not/AZone")


def test_make_aware_datetime_applies_correct_offset():
    dt = make_aware_datetime(date(2026, 9, 10), time(9, 0), "Asia/Kolkata")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(hours=5, minutes=30)
    assert dt.hour == 9 and dt.minute == 0


def test_make_aware_datetime_raises_for_invalid_timezone():
    with pytest.raises(ValueError):
        make_aware_datetime(date(2026, 9, 10), time(9, 0), "Not/AZone")


def test_ensure_aware_datetime_passes_through_aware_value():
    aware = datetime(2026, 9, 10, 9, 0, tzinfo=ZoneInfo("UTC"))
    result = ensure_aware_datetime(aware, "Asia/Kolkata")
    assert result is aware


def test_ensure_aware_datetime_localizes_naive_value():
    naive = datetime(2026, 9, 10, 9, 0)
    result = ensure_aware_datetime(naive, "Asia/Kolkata")
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(hours=5, minutes=30)


def test_convert_to_timezone_preserves_the_same_instant():
    kolkata = make_aware_datetime(date(2026, 9, 10), time(9, 0), "Asia/Kolkata")
    ny = convert_to_timezone(kolkata, "America/New_York")
    assert kolkata == ny  # same instant, different representation
    assert ny.utcoffset() == timedelta(hours=-4)  # EDT in September


def test_convert_to_timezone_rejects_naive_datetime():
    with pytest.raises(ValueError):
        convert_to_timezone(datetime(2026, 9, 10, 9, 0), "Asia/Kolkata")


def _at(hour_float: float) -> datetime:
    hour = int(hour_float)
    minute = int(round((hour_float - hour) * 60))
    return datetime(2026, 9, 10, hour, minute, tzinfo=ZoneInfo("UTC"))


@pytest.mark.parametrize(
    "a_start,a_end,b_start,b_end,expected",
    [
        (9, 10, 10, 11, False),  # back-to-back, half-open ranges don't overlap
        (9, 10, 9.5, 10.5, True),  # partial overlap
        (9, 11, 9.5, 10.5, True),  # fully contains
        (9, 10, 11, 12, False),  # disjoint
        (10, 11, 9, 10, False),  # back-to-back, other direction
        (9, 10, 9, 10, True),  # identical ranges
    ],
)
def test_overlaps(a_start, a_end, b_start, b_end, expected):
    assert overlaps(_at(a_start), _at(a_end), _at(b_start), _at(b_end)) is expected


def test_get_doctor_timezone_returns_valid_stored_value(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO doctors (name, timezone) VALUES (%s, %s) RETURNING id",
            ("Tz Test Doctor", "America/New_York"),
        )
        doctor_id = cur.fetchone()[0]
    db_connection.commit()

    with db_connection.cursor() as cur:
        tz = get_doctor_timezone(cur, doctor_id)

    assert tz == "America/New_York"


def test_get_doctor_timezone_falls_back_for_invalid_stored_value(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO doctors (name, timezone) VALUES (%s, %s) RETURNING id",
            ("Tz Test Doctor Invalid", "Not/AZone"),
        )
        doctor_id = cur.fetchone()[0]
    db_connection.commit()

    with db_connection.cursor() as cur:
        tz = get_doctor_timezone(cur, doctor_id)

    assert tz == "Asia/Kolkata"


def test_get_doctor_timezone_raises_for_missing_doctor(db_connection):
    with db_connection.cursor() as cur:
        with pytest.raises(Exception):
            get_doctor_timezone(cur, 999_999)
