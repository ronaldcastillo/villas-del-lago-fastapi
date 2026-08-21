import json
import logging
from typing import Literal

import openai
from fastapi import APIRouter, Depends
from google.api_core.exceptions import PermissionDenied
from pydantic import BaseModel

from app.auth import Principal, staff_user
from app.config import CODE, ERR, settings
from app.limits import enforce, extraction_limiter
from app.responses import ApiError, ok
from app.services import document_ai, vision
from app.services.openai_client import parse_id_text
from app.services.storage import ID_DOCUMENT_KIND, save_to_storage
from app.utils import decode_document, file_extension, sanitize_document_id, unique_filename

log = logging.getLogger("vdl.documents")
router = APIRouter(prefix="/documents", tags=["documents"])


class ExtractionBody(BaseModel):
    document: str | None = None
    mimeType: str | None = None
    engine: Literal["document-ai", "vision-ai"] = "document-ai"


def _is_permission(e):
    return isinstance(e, PermissionDenied) or "permission" in str(e)


def _save_original(extracted, data, mime_type, fn):
    # best effort — storage failing shouldn't fail the whole request
    if not extracted.get("documentId"):
        return
    try:
        filename = unique_filename(file_extension(mime_type))
        extracted["documentUrl"] = save_to_storage(
            data, filename, content_type=mime_type,
            metadata={"kind": ID_DOCUMENT_KIND},
        )
    except Exception:
        log.warning("%s storage save failed", fn, exc_info=True)
        extracted["documentUrl"] = None


@router.post("/extractions", status_code=201)
def create_extraction(body: ExtractionBody, principal: Principal = Depends(staff_user)):
    # staff only — this burns Document AI / Vision / OpenAI quota per call and
    # writes caller-supplied bytes into the bucket
    enforce(extraction_limiter, principal.uid, tag="extraction")
    # pulls {documentId, name, dob} out of an ID document; engine picks the pipeline
    data = decode_document(body.document, body.mimeType)
    extract = _with_vision_ai if body.engine == "vision-ai" else _with_document_ai
    extracted = extract(data, body.mimeType)
    log.info("extraction[%s] completed, %d field(s) found", body.engine, sum(1 for v in extracted.values() if v))
    return ok(extracted)


def _with_document_ai(data, mime_type):
    fn = "extraction[document-ai]"
    try:
        result = document_ai.process_document(data, mime_type)
    except Exception as e:
        log.exception("%s error", fn)
        if _is_permission(e):
            raise ApiError(403, ERR.DOCUMENT_AI_AUTH_ERROR, CODE.AUTH_ERROR)
        raise ApiError(500, ERR.INTERNAL_ERROR, CODE.DOCUMENT_AI_ERROR)

    extracted = document_ai.extract_entities(result)
    _save_original(extracted, data, mime_type, fn)
    return extracted


def _with_vision_ai(data, mime_type):
    fn = "extraction[vision-ai]"
    if not settings.openai_api_key:
        raise ApiError(500, ERR.OPENAI_NOT_CONFIGURED, CODE.CONFIG_ERROR)

    try:
        text = vision.extract_text(data)
        log.info("%s vision done, %d chars", fn, len(text))
        if not text:
            raise ApiError(400, ERR.NO_TEXT_EXTRACTED, CODE.NO_TEXT_ERROR)
        raw = parse_id_text(text)
    except ApiError:
        raise
    except Exception as e:
        log.exception("%s error", fn)
        if _is_permission(e):
            raise ApiError(403, ERR.VISION_API_AUTH_ERROR, CODE.AUTH_ERROR)
        if isinstance(e, openai.OpenAIError) or "openai" in str(e).lower():
            raise ApiError(400, "OpenAI API error", CODE.OPENAI_ERROR)
        raise ApiError(500, ERR.INTERNAL_ERROR, CODE.VISION_AI_ERROR)

    extracted = {"documentId": None, "name": None, "dob": None}
    try:
        parsed = json.loads(raw)
        extracted = {
            "documentId": sanitize_document_id(parsed.get("documentId") or parsed.get("id")) or None,
            "name": parsed.get("name") or None,
            "dob": parsed.get("dob") or None,
        }
    except Exception:
        # model didn't give us clean json — return nulls like before
        log.warning("%s failed to parse OpenAI response", fn)

    _save_original(extracted, data, mime_type, fn)
    return extracted
