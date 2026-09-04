"""Unit tests for app/utils/phone.py -- pure logic, no HTTP/DB involved."""

from app.utils.phone import normalize_whatsapp_number


def test_bare_10_digit_number_gets_plus_91_prefix():
    assert normalize_whatsapp_number("9876543210") == "+919876543210"


def test_already_e164_number_is_unchanged():
    assert normalize_whatsapp_number("+919876543210") == "+919876543210"


def test_spaces_are_stripped():
    assert normalize_whatsapp_number("+91 98765 43210") == "+919876543210"
    assert normalize_whatsapp_number("91 9876543210") == "+919876543210"


def test_hyphens_and_parentheses_are_stripped():
    assert normalize_whatsapp_number("+91-98765-43210") == "+919876543210"
    assert normalize_whatsapp_number("(+91) 9876543210") == "+919876543210"


def test_91_prefixed_without_plus_gets_plus():
    assert normalize_whatsapp_number("919876543210") == "+919876543210"


def test_00_prefix_is_treated_as_international_dialing_prefix():
    assert normalize_whatsapp_number("0091 9876543210") == "+919876543210"


def test_all_documented_format_variants_normalize_identically():
    variants = ["9876543210", "+919876543210", "+91 9876543210", "91-9876543210"]
    normalized = {normalize_whatsapp_number(v) for v in variants}
    assert normalized == {"+919876543210"}


def test_non_indian_looking_number_is_not_guessed_at():
    # Already has a country code shape this function doesn't recognize
    # (not 10 bare digits, not a bare '91' + 10 digits) -- left alone
    # rather than corrupted by a wrong prefix guess.
    assert normalize_whatsapp_number("+14155552671") == "+14155552671"
