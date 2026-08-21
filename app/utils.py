import base64
import binascii
import re
import secrets
import time
from datetime import datetime, timezone

from app.config import CODE, ERR, MIME_TYPE_EXTENSIONS, settings
from app.responses import ApiError


def now_ms():
    # everything in firestore is epoch ms, same as the node side
    return int(time.time() * 1000)


def decode_document(document, mime_type):
    # returns raw bytes or raises the same 400s the node version did
    if not document:
        raise ApiError(400, ERR.MISSING_DOCUMENT, CODE.VALIDATION_ERROR)
    if not mime_type:
        raise ApiError(400, ERR.MISSING_MIME_TYPE, CODE.VALIDATION_ERROR)
    # check the *encoded* size first — decoding a multi-hundred-MB string to
    # then reject it is the memory exhaustion, not the result
    if len(document) > settings.max_document_size * 4 // 3 + 8:
        raise ApiError(400, ERR.DOCUMENT_TOO_LARGE, CODE.VALIDATION_ERROR)
    try:
        data = base64.b64decode(document, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, ERR.INVALID_BASE64, CODE.VALIDATION_ERROR)
    if len(data) > settings.max_document_size:
        raise ApiError(400, ERR.DOCUMENT_TOO_LARGE, CODE.VALIDATION_ERROR)
    return data


def file_extension(mime_type):
    return MIME_TYPE_EXTENSIONS.get(mime_type, ".bin")


def unique_filename(extension):
    # deliberately opaque: the old {cedula}_{rand} form leaked the national id
    # into the object path, the returned url, logs and Referer headers
    return f"{secrets.token_hex(16)}{extension}"


def format_name(name):
    if not name:
        return None
    clean = re.sub(r"\s+", " ", name.replace("\n", " ")).strip()
    return " ".join(w[:1].upper() + w[1:] for w in clean.lower().split(" "))


MIN_PHONE_DIGITS = 7
MAX_PHONE_DIGITS = 15  # E.164 ceiling


def sanitize_phone(phone):
    # normalises to bare digits: drops twilio's "whatsapp:" prefix, the +1
    # country code, and any separators. the old version only handled the
    # prefix, so "(809) 555-1234" never matched a stored number.
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    # DR numbers are +1 809/829/849 — keep the national 10 digits
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def is_plausible_phone(phone):
    return MIN_PHONE_DIGITS <= len(phone or "") <= MAX_PHONE_DIGITS


def sanitize_document_id(document_id):
    if not document_id:
        return None
    digits = re.sub(r"\D", "", str(document_id))
    return digits or None


def es_date(ms):
    # mimic JS toLocaleDateString("es-DO") -> d/M/yyyy, no zero padding
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return "N/A"
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f"{d.day}/{d.month}/{d.year}"


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]
