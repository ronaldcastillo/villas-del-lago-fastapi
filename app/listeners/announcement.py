import logging

from app.config import COLLECTIONS
from app.firebase import get_db
from app.services.fcm import all_tokens, send_to_tokens

log = logging.getLogger("vdl.listeners.announcement")

MAX_BODY = 3500  # fcm body limit varies by platform, stay under
DEFAULT_TITLE = "Anuncio"


def _truncate(msg):
    return msg if len(msg) <= MAX_BODY else msg[: MAX_BODY - 1] + "…"


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def broadcast(snap):
    # one push per updatedAt — also short-circuits after we merge lastNotifiedAt
    after = snap.to_dict() or {}
    updated_at = after.get("updatedAt")
    if not _is_number(updated_at):
        log.warning("announcement missing numeric updatedAt, skipping push")
        return
    if after.get("lastNotifiedAt") == updated_at:
        return
    if not after.get("enabled"):
        return
    message = after.get("message")
    message = message.strip() if isinstance(message, str) else ""
    if not message:
        return

    title = after.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else DEFAULT_TITLE

    entries = all_tokens()
    if entries:
        data = {"type": "announcement", "updatedAt": str(updated_at)}
        send_to_tokens(entries, title, _truncate(message), data=data, tag="announcement")
    else:
        log.info("no fcm tokens registered; updating lastNotifiedAt only")

    snap.reference.set({"lastNotifiedAt": updated_at}, merge=True)


def _callback(docs, changes, read_time):
    try:
        if not docs:
            return  # doc deleted / doesn't exist
        broadcast(docs[0])
    except Exception:
        log.exception("announcement watch callback failed")


def start():
    # initial snapshot is processed too — harmless thanks to the lastNotifiedAt guard
    return get_db().document(f"{COLLECTIONS.SITE_CONFIG}/{COLLECTIONS.ANNOUNCEMENT_DOC}").on_snapshot(_callback)
