import logging
from datetime import timedelta
from urllib.parse import quote

from google.cloud import storage

from app.config import settings

log = logging.getLogger("vdl.storage")

_client = None


def _storage():
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


ID_DOCUMENT_KIND = "id-document"


def save_to_storage(data: bytes, filename: str, content_type: str | None = None,
                    metadata: dict | None = None) -> str:
    blob = _storage().bucket(settings.storage_bucket).blob(filename)
    if metadata:
        # custom metadata rather than a path prefix, so object urls are unchanged
        blob.metadata = metadata
    blob.upload_from_string(data, content_type=content_type)

    if settings.use_signed_urls:
        # requires the runtime service account to hold roles/iam.serviceAccountTokenCreator
        # on itself, since ADC on cloud run has no private key to sign with
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=settings.signed_url_ttl_minutes),
            method="GET",
        )

    # legacy public form the app already knows how to read — only resolves if the
    # bucket grants public read, which is exactly what should be revoked once
    # USE_SIGNED_URLS is switched on
    return f"https://firebasestorage.googleapis.com/v0/b/{settings.storage_bucket}/o/{quote(filename, safe='')}?alt=media"


def delete_expired_documents(older_than_ms: int, kind: str = ID_DOCUMENT_KIND) -> int:
    """Delete stored objects tagged `kind` that were created before `older_than_ms`."""
    from datetime import datetime, timezone

    cutoff = datetime.fromtimestamp(older_than_ms / 1000, tz=timezone.utc)
    removed = 0
    for blob in _storage().bucket(settings.storage_bucket).list_blobs():
        if (blob.metadata or {}).get("kind") != kind:
            continue
        if blob.time_created and blob.time_created >= cutoff:
            continue
        try:
            blob.delete()
            removed += 1
        except Exception:
            log.warning("could not delete %s", blob.name, exc_info=True)
    return removed
