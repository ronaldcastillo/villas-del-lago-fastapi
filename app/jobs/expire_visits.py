import logging

from google.cloud.firestore_v1.base_query import FieldFilter

from app.config import COLLECTIONS
from app.firebase import get_db
from app.utils import chunks, now_ms

log = logging.getLogger("vdl.jobs.expire")


def expire_visits():
    # pending visits past expiresAt get closed out as expired
    log.info("expireVisits started")
    try:
        db = get_db()
        now = now_ms()
        docs = (
            db.collection(COLLECTIONS.VISITORS)
            .where(filter=FieldFilter("completed", "==", False))
            .where(filter=FieldFilter("expiresAt", "<=", now))
            .get()
        )
        if not docs:
            log.info("expireVisits: nothing to expire")
            return 0

        patch = {"completed": True, "expired": True, "expiredAt": now, "updatedAt": now}
        for group in chunks(list(docs), 500):  # batch cap
            batch = db.batch()
            for d in group:
                batch.update(d.reference, patch)
            batch.commit()
        log.info("expireVisits: expired %d visit(s)", len(docs))
        return len(docs)
    except Exception:
        log.exception("expireVisits error")
        return 0
