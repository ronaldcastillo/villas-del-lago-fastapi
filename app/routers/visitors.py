import io
import logging

import qrcode
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import Principal, user_or_service
from app.config import CODE, COLLECTIONS, ERR, settings
from app.firebase import get_db
from app.responses import ApiError, ok
from app.services.storage import save_to_storage
from app.utils import now_ms, unique_filename

log = logging.getLogger("vdl.visitors")
router = APIRouter(prefix="/visitors", tags=["visitors"])


class CreateVisitorBody(BaseModel):
    name: str | None = None
    unitNumber: str | None = None
    reportedBy: str | None = None
    reportedByNumber: str | None = None
    userId: str | None = None


def _attribution(body: CreateVisitorBody, principal: Principal) -> dict:
    # n8n (whatsapp) still supplies the reporter; an app user never does —
    # otherwise the audit trail is whatever the caller types
    if principal.is_service:
        return {
            "unitNumber": body.unitNumber,
            "reportedBy": body.reportedBy or None,
            "reportedByNumber": body.reportedByNumber or None,
            "userId": body.userId or None,
            "createdBy": body.userId or None,
            "source": "whatsapp",
        }

    if not principal.is_staff and body.unitNumber != principal.unit_number:
        raise ApiError(403, ERR.UNIT_NOT_ALLOWED, CODE.FORBIDDEN)

    return {
        "unitNumber": body.unitNumber,
        "reportedBy": principal.name,
        "reportedByNumber": principal.phone_number,
        "userId": principal.uid,
        "createdBy": principal.uid,
        "source": "app",
    }


@router.post("", status_code=201)
def create_visitor(body: CreateVisitorBody, principal: Principal = Depends(user_or_service)):
    log.info("createVisitor started service=%s", principal.is_service)
    if not body.unitNumber:
        raise ApiError(400, ERR.MISSING_UNIT_NUMBER, CODE.VALIDATION_ERROR)

    created_at = now_ms()
    data = {
        "name": body.name or None,
        "documentId": None,
        "dob": None,
        "completed": False,
        "serviceType": None,
        "createdAt": created_at,
        "expiresAt": created_at + settings.visit_expiration_ms,
        **_attribution(body, principal),
    }

    try:
        _, ref = get_db().collection(COLLECTIONS.VISITORS).add(data)
        snap = ref.get()

        # qr payload is still just the doc id — the gate app resolves it and
        # re-checks completed/expiresAt, so the id is a lookup key, not a token
        buf = io.BytesIO()
        qrcode.make(snap.id).save(buf, format="PNG")
        name = unique_filename(".png") if settings.opaque_qr_filenames else f"{snap.id}.png"
        qr_url = save_to_storage(buf.getvalue(), name, content_type="image/png")
        # store it so nothing downstream has to rebuild the object path
        ref.update({"qr": qr_url})
    except Exception:
        log.exception("createVisitor error")
        raise ApiError(500, ERR.INTERNAL_ERROR, CODE.CREATE_VISITOR_ERROR)

    log.info("createVisitor completed visitorId=%s", snap.id)
    return ok({"id": snap.id, **(snap.to_dict() or {}), "qr": qr_url})
