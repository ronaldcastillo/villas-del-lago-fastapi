import logging

from firebase_admin import exceptions as fb_exc
from firebase_admin import messaging
from google.cloud.firestore_v1.base_query import FieldFilter

from app.config import COLLECTIONS
from app.firebase import get_db
from app.utils import chunks

log = logging.getLogger("vdl.fcm")

MULTICAST_CHUNK = 500
BATCH_SIZE = 500


def tokens_for_users(user_ids):
    # -> [(doc_ref, token)], firestore 'in' caps at 30
    db = get_db()
    out = []
    for chunk in chunks(list(user_ids), 30):
        docs = db.collection(COLLECTIONS.FCM_TOKENS).where(filter=FieldFilter("userId", "in", chunk)).get()
        for d in docs:
            token = (d.to_dict() or {}).get("token") or d.id
            if token:
                out.append((d.reference, token))
    return out


def all_tokens():
    # app stores the token as the doc id, older docs only have the field — take either
    out = []
    for d in get_db().collection(COLLECTIONS.FCM_TOKENS).get():
        token = d.id or (d.to_dict() or {}).get("token")
        if isinstance(token, str) and token:
            out.append((d.reference, token))
    return out


def _dead_token(exc):
    if isinstance(exc, messaging.UnregisteredError):
        return True
    return isinstance(exc, fb_exc.InvalidArgumentError) and "registration token" in str(exc).lower()


def send_to_tokens(entries, title, body, data=None, tag="fcm"):
    # entries = [(doc_ref, token)]; sends in chunks of 500 and prunes dead tokens
    sent = failed = 0
    dead = []
    for chunk in chunks(list(entries), MULTICAST_CHUNK):
        resp = messaging.send_each_for_multicast(
            messaging.MulticastMessage(
                fids=[t for _, t in chunk],  # fcm's new name for tokens
                notification=messaging.Notification(title=title, body=body),
                data=data,
                webpush=messaging.WebpushConfig(fcm_options=messaging.WebpushFCMOptions(link="/")),
            )
        )
        sent += resp.success_count
        failed += resp.failure_count
        for (ref, _), r in zip(chunk, resp.responses):
            if r.success:
                continue
            log.warning("%s: send failed: %s", tag, r.exception)
            if _dead_token(r.exception):
                dead.append(ref)

    if dead:
        db = get_db()
        for slice_ in chunks(dead, BATCH_SIZE):
            batch = db.batch()
            for ref in slice_:
                batch.delete(ref)
            batch.commit()
        log.info("%s: removed %d invalid token(s)", tag, len(dead))

    log.info("%s: %d sent, %d failed", tag, sent, failed)
    return sent, failed
