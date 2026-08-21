import logging

from app.config import settings
from app.services.storage import delete_expired_documents
from app.utils import now_ms

log = logging.getLogger("vdl.jobs.purge")

DAY_MS = 24 * 60 * 60 * 1000


def purge_documents():
    # scanned cedulas otherwise accumulate in the bucket forever
    days = settings.document_retention_days
    if days <= 0:
        return 0

    cutoff = now_ms() - days * DAY_MS
    log.info("purgeDocuments started retention=%dd", days)
    try:
        removed = delete_expired_documents(cutoff)
    except Exception:
        log.exception("purgeDocuments error")
        return 0

    log.info("purgeDocuments: removed %d document(s)", removed)
    return removed
