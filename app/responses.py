import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import CODE, ERR

log = logging.getLogger("vdl.errors")


def _ts():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ok(data, message=None):
    body = {"success": True, "data": data}
    if message:
        body["message"] = message
    body["timestamp"] = _ts()
    return body


def error_body(message, status_code=500, code=None):
    err = {"message": message, "statusCode": status_code}
    if code:
        err["code"] = code
    err["timestamp"] = _ts()
    return {"success": False, "error": err}


class ApiError(Exception):
    # raise this anywhere in a route; handler below turns it into the node-style envelope
    def __init__(self, status_code, message, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def install_handlers(app: FastAPI):
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(error_body(exc.message, exc.status_code, exc.code), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        msg = f"{loc}: {first.get('msg')}" if loc else str(first.get("msg", "Invalid request"))
        return JSONResponse(error_body(msg, 400, CODE.VALIDATION_ERROR), status_code=400)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # never hand the client an exception string — google errors carry
        # project ids, resource paths and query shapes
        ref = uuid.uuid4().hex[:12]
        log.exception("unhandled error ref=%s path=%s", ref, request.url.path)
        body = error_body(ERR.INTERNAL_ERROR, 500, CODE.INTERNAL_ERROR)
        body["error"]["ref"] = ref
        return JSONResponse(body, status_code=500)
