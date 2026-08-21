import logging

from fastapi import APIRouter, Depends, Query
from google.cloud.firestore_v1.base_query import FieldFilter

from app.auth import Principal, service_caller
from app.config import CODE, COLLECTIONS, ERR
from app.firebase import get_db
from app.responses import ApiError, ok
from app.utils import is_plausible_phone, sanitize_phone

log = logging.getLogger("vdl.profiles")
router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("")
def get_profile_by_phone(
    phoneNumber: str | None = Query(None),
    _: Principal = Depends(service_caller),
):
    # service-only: this is n8n's identity lookup for the whatsapp flow.
    # left open it is a phone-number oracle over the whole resident directory.
    log.info("getProfileByPhone started")
    if not phoneNumber:
        raise ApiError(400, ERR.MISSING_PHONE_NUMBER, CODE.VALIDATION_ERROR)

    phone = sanitize_phone(phoneNumber)
    if not is_plausible_phone(phone):
        raise ApiError(400, ERR.MISSING_PHONE_NUMBER, CODE.VALIDATION_ERROR)

    try:
        docs = get_db().collection(COLLECTIONS.AUTHORIZED_USERS).where(filter=FieldFilter("phoneNumber", "==", phone)).get()
    except Exception:
        log.exception("getProfileByPhone error")
        raise ApiError(500, ERR.INTERNAL_ERROR, CODE.RETRIEVE_PROFILE_ERROR)

    if not docs:
        # node version used VALIDATION_ERROR here too, keeping it
        raise ApiError(404, ERR.PROFILE_NOT_FOUND, CODE.VALIDATION_ERROR)

    # last match wins, same as the forEach in node
    profile = None
    for d in docs:
        p = d.to_dict() or {}
        profile = {
            "id": d.id,
            "name": p.get("name"),
            "unitNumber": p.get("unitNumber"),
            "isActive": p.get("isActive"),
            "role": p.get("role") or None,
        }

    log.info("getProfileByPhone completed profileId=%s", profile["id"])
    return ok(profile)
