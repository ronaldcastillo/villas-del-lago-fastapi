import base64
import re

import pytest

from app.responses import ApiError
from app.utils import (decode_document, es_date, format_name, is_plausible_phone,
                       sanitize_document_id, sanitize_phone, unique_filename)


def test_sanitize_phone():
    assert sanitize_phone("whatsapp:+18095551234") == "8095551234"
    assert sanitize_phone("18095551234") == "8095551234"
    assert sanitize_phone(" 8095551234 ") == "8095551234"
    assert sanitize_phone(None) == ""


def test_format_name():
    assert format_name("JUAN\nPEREZ   GOMEZ") == "Juan Perez Gomez"
    assert format_name("") is None


def test_sanitize_document_id():
    assert sanitize_document_id("001-1234567-8") == "00112345678"
    assert sanitize_document_id("abc") is None
    assert sanitize_document_id(None) is None


def test_es_date():
    assert es_date(1755648000000) == "20/8/2025"  # d/M/yyyy, no padding
    assert es_date(None) == "N/A"
    assert es_date(True) == "N/A"


def test_unique_filename_is_opaque():
    name = unique_filename(".jpg")
    assert re.fullmatch(r"[0-9a-f]{32}\.jpg", name)
    assert unique_filename(".jpg") != name


def test_decode_document_errors():
    with pytest.raises(ApiError) as e:
        decode_document(None, "image/png")
    assert e.value.status_code == 400
    with pytest.raises(ApiError):
        decode_document("aGk=", None)
    with pytest.raises(ApiError):
        decode_document("%%%notbase64", "image/png")


def test_decode_document_ok():
    assert decode_document(base64.b64encode(b"hi").decode(), "image/png") == b"hi"


def test_decode_document_rejects_oversize_before_decoding(monkeypatch):
    # the guard must fire on the encoded string, not on the decoded bytes
    from app import utils
    monkeypatch.setattr(utils.settings, "max_document_size", 16)
    with pytest.raises(ApiError) as e:
        decode_document("A" * 10_000, "image/png")
    assert e.value.status_code == 400 and "size" in e.value.message.lower()


def test_sanitize_phone_strips_separators():
    # the old version only handled the whatsapp: prefix, so a formatted
    # number never matched a stored one
    assert sanitize_phone("(809) 555-1234") == "8095551234"
    assert sanitize_phone("+1 809-555-1234") == "8095551234"
    assert sanitize_phone("809.555.1234") == "8095551234"


def test_is_plausible_phone():
    assert is_plausible_phone("8095551234")
    assert not is_plausible_phone("")
    assert not is_plausible_phone("123")
    assert not is_plausible_phone("9" * 20)
