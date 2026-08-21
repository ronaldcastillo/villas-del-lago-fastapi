import logging
import secrets as _secrets
from dataclasses import dataclass

from fastapi import Depends, Header
from firebase_admin import auth as fb_auth
from google.cloud.firestore_v1.base_query import FieldFilter

from app.config import CODE, COLLECTIONS, ERR, settings
from app.firebase import get_db
from app.responses import ApiError

log = logging.getLogger("vdl.auth")

STAFF_ROLES = ("admin", "security")


@dataclass(frozen=True)
class Principal:
    # the only trusted identity in the app — never built from a request body
    uid: str
    role: str | None = None
    unit_number: str | None = None
    name: str | None = None
    phone_number: str | None = None
    is_service: bool = False

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    def chat_context(self) -> dict:
        # replaces the client-supplied userContext the node version accepted
        return {
            "userId": self.uid,
            "role": self.role,
            "name": self.name,
            "unitNumber": self.unit_number,
            "phoneNumber": self.phone_number,
        }


SERVICE_PRINCIPAL = Principal(uid="n8n", role="service", is_service=True)


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise ApiError(401, ERR.MISSING_CREDENTIALS, CODE.AUTH_ERROR)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, ERR.INVALID_CREDENTIALS, CODE.AUTH_ERROR)
    return token.strip()


def _profile_for(uid: str) -> dict | None:
    # the rest of the app treats the authorizedUsers doc id as userId
    # (see listeners/visitors.py _active_user_ids, chat/queries.my_visitors),
    # so prefer a doc-id hit and fall back to a `uid` field for profiles
    # that were created before auth existed
    db = get_db()
    snap = db.collection(COLLECTIONS.AUTHORIZED_USERS).document(uid).get()
    if getattr(snap, "exists", False):
        return {"id": snap.id, **(snap.to_dict() or {})}

    for d in db.collection(COLLECTIONS.AUTHORIZED_USERS).where(filter=FieldFilter("uid", "==", uid)).get():
        return {"id": d.id, **(d.to_dict() or {})}
    return None


def current_user(authorization: str | None = Header(None)) -> Principal:
    token = _bearer(authorization)
    try:
        decoded = fb_auth.verify_id_token(token)
    except Exception:
        # don't echo the reason back — expired vs malformed is a fingerprinting aid
        log.info("rejected firebase id token", exc_info=True)
        raise ApiError(401, ERR.INVALID_CREDENTIALS, CODE.AUTH_ERROR)

    uid = decoded.get("uid") or decoded.get("sub")
    if not uid:
        raise ApiError(401, ERR.INVALID_CREDENTIALS, CODE.AUTH_ERROR)

    profile = _profile_for(uid)
    if profile is None:
        log.info("authenticated uid has no authorizedUsers profile")
        raise ApiError(403, ERR.PROFILE_NOT_FOUND, CODE.FORBIDDEN)
    if profile.get("isActive") is False:
        raise ApiError(403, ERR.PROFILE_INACTIVE, CODE.FORBIDDEN)

    return Principal(
        uid=profile["id"],
        role=profile.get("role") or "user",
        unit_number=profile.get("unitNumber"),
        name=profile.get("name"),
        phone_number=profile.get("phoneNumber"),
    )


def service_caller(x_service_key: str | None = Header(None)) -> Principal:
    # n8n holds the whatsapp side; it sends X-Service-Key
    expected = settings.service_api_key
    if not expected:
        log.error("service_api_key is unset — refusing service-authenticated request")
        raise ApiError(503, ERR.SERVICE_AUTH_NOT_CONFIGURED, CODE.CONFIG_ERROR)
    # compare on bytes: compare_digest raises TypeError on non-ascii str,
    # which would turn a bad header into a 500
    if not x_service_key or not _secrets.compare_digest(x_service_key.encode(), expected.encode()):
        raise ApiError(401, ERR.INVALID_CREDENTIALS, CODE.AUTH_ERROR)
    return SERVICE_PRINCIPAL


def user_or_service(
    authorization: str | None = Header(None),
    x_service_key: str | None = Header(None),
) -> Principal:
    # /visitors is reachable both from the app (resident) and from n8n (whatsapp)
    if x_service_key:
        return service_caller(x_service_key)
    return current_user(authorization)


def staff_user(principal: Principal = Depends(current_user)) -> Principal:
    if not principal.is_staff:
        raise ApiError(403, ERR.FORBIDDEN, CODE.FORBIDDEN)
    return principal
