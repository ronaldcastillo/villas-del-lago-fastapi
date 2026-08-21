import logging

from google.cloud.firestore_v1 import Query
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.watch import ChangeType

from app.config import COLLECTIONS, settings
from app.firebase import get_db
from app.services.fcm import send_to_tokens, tokens_for_users

log = logging.getLogger("vdl.listeners.visitors")


def _active_user_ids(**filters):
    q = get_db().collection(COLLECTIONS.AUTHORIZED_USERS)
    for field, value in filters.items():
        q = q.where(filter=FieldFilter(field, "==", value))
    return [d.id for d in q.where(filter=FieldFilter("isActive", "==", True)).get()]


def notify_security(visitor: dict):
    unit = visitor.get("unitNumber")
    if not unit:
        log.warning("visitor missing unitNumber, skipping security push")
        return
    users = _active_user_ids(role="security")
    if not users:
        log.info("no active security users")
        return
    entries = tokens_for_users(users)
    if not entries:
        log.info("no fcm tokens for security users")
        return
    name = visitor.get("name") or "Un visitante"
    send_to_tokens(entries, "Nueva Visita", f"{name} - Unidad {unit}", tag="new-visitor")


def notify_residents(visitor: dict):
    unit = visitor.get("unitNumber")
    if not unit:
        log.warning("visitor missing unitNumber, skipping arrival push")
        return
    users = _active_user_ids(unitNumber=unit)
    if not users:
        log.info("no active residents for unit %s", unit)
        return
    entries = tokens_for_users(users)
    if not entries:
        log.info("no fcm tokens for unit %s residents", unit)
        return
    name = visitor.get("name") or "Tu visitante"
    send_to_tokens(entries, "Tu visitante ha llegado", f"{name} ha llegado al residencial", tag="arrival")


def _is_expired(d: dict):
    # expireVisits flips completed too — don't ping residents for those
    return d.get("expired") is True or d.get("expiredAt") is not None


class VisitorWatcher:
    # replaces onDocumentCreated + onDocumentUpdated(false->true) on visitors/{id}
    # watches the N most recent visitors and remembers their completed flag

    def __init__(self):
        self.completed = {}
        self.started_at = None

    def handle(self, docs, changes, read_time):
        if self.started_at is None:
            # first snapshot is just "what's already there", don't notify
            self.started_at = read_time
            for d in docs:
                self.completed[d.id] = bool((d.to_dict() or {}).get("completed"))
            log.info("visitor watch ready, tracking %d doc(s)", len(docs))
            return

        for ch in changes:
            try:
                self._apply(ch)
            except Exception:
                # one bad push shouldn't drop the rest of the batch
                log.exception("visitor change failed")

    def _apply(self, ch):
        snap = ch.document
        data = snap.to_dict() or {}
        if ch.type == ChangeType.ADDED:
            self.completed[snap.id] = bool(data.get("completed"))
            # create_time is server-side, so docs that merely slid into the window don't count as new
            if snap.create_time and snap.create_time >= self.started_at:
                notify_security(data)
        elif ch.type == ChangeType.MODIFIED:
            was = self.completed.get(snap.id, False)
            now = data.get("completed") is True
            self.completed[snap.id] = now
            if not was and now and not _is_expired(data):
                notify_residents(data)
        elif ch.type == ChangeType.REMOVED:
            self.completed.pop(snap.id, None)

    def __call__(self, docs, changes, read_time):
        try:
            self.handle(docs, changes, read_time)
        except Exception:
            # an exception escaping here kills the watch thread silently
            log.exception("visitor watch callback failed")


def start():
    q = (
        get_db().collection(COLLECTIONS.VISITORS)
        .order_by("createdAt", direction=Query.DESCENDING)
        .limit(settings.visitor_watch_window)
    )
    return q.on_snapshot(VisitorWatcher())
