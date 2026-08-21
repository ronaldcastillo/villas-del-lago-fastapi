import logging

from fastapi import APIRouter, Body, Depends

from app.auth import Principal, current_user
from app.chat.runner import run_chat
from app.config import CODE, ERR, settings
from app.limits import chat_limiter, enforce
from app.responses import ApiError, ok

log = logging.getLogger("vdl.chat")
router = APIRouter(prefix="/chat", tags=["chat"])


def _valid_messages(messages):
    return (
        isinstance(messages, list)
        and len(messages) > 0
        and all(isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) for m in messages)
    )


@router.post("")
def chat(body: dict = Body(...), principal: Principal = Depends(current_user)):
    log.info("chat started role=%s", principal.role)
    enforce(chat_limiter, principal.uid, tag="chat")

    messages = body.get("messages")
    if not _valid_messages(messages):
        raise ApiError(400, ERR.INVALID_MESSAGES, CODE.VALIDATION_ERROR)
    if any(len(m["content"]) > settings.max_chat_message_chars for m in messages):
        raise ApiError(400, ERR.MESSAGE_TOO_LONG, CODE.VALIDATION_ERROR)

    # userContext is deliberately NOT read from the body — role and userId decide
    # which firestore tools run, so they come from the verified token only
    try:
        return ok(run_chat(messages, principal.chat_context()))
    except ApiError:
        raise
    except Exception:
        log.exception("chat error")
        raise ApiError(500, ERR.INTERNAL_ERROR, CODE.CHAT_ASSISTANT_ERROR)
