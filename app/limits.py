import logging
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import CODE, ERR, settings
from app.responses import ApiError, error_body

log = logging.getLogger("vdl.limits")

MAX_TRACKED_KEYS = 20_000


class RateLimiter:
    # token bucket; safe to keep in-process because the service is pinned to a
    # single always-on instance for the firestore listeners (see README deploy)
    def __init__(self, limit: int, window: int):
        self.limit = max(1, limit)
        self.window = max(1, window)
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        rate = self.limit / self.window
        with self._lock:
            if len(self._buckets) > MAX_TRACKED_KEYS:
                self._prune(now)
            tokens, last = self._buckets.get(key, (float(self.limit), now))
            tokens = min(float(self.limit), tokens + (now - last) * rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True

    def _prune(self, now: float) -> None:
        # drop anything already back to a full bucket — cheap and keeps memory flat
        stale = [k for k, (_, last) in self._buckets.items() if now - last > self.window]
        for k in stale:
            self._buckets.pop(k, None)
        if len(self._buckets) > MAX_TRACKED_KEYS:
            self._buckets.clear()


ip_limiter = RateLimiter(settings.ip_rate_limit, settings.ip_rate_window)
chat_limiter = RateLimiter(settings.chat_rate_limit, settings.chat_rate_window)
extraction_limiter = RateLimiter(settings.extraction_rate_limit, settings.extraction_rate_window)


def enforce(limiter: RateLimiter, key: str, tag: str = "rate") -> None:
    if not limiter.allow(key):
        log.warning("%s: rate limited", tag)
        raise ApiError(429, ERR.RATE_LIMITED, CODE.RATE_LIMITED)


def _client_ip(request) -> str:
    # X-Forwarded-For is caller-controlled at the LEFT end — a client can send
    # its own header and the proxies append after it. Only the entries appended
    # by our own trusted proxies are trustworthy, so index from the right.
    #   cloud run direct:            "<spoofable...>, <client>"        hops=1
    #   behind an external https lb: "<spoofable...>, <client>, <lb>"  hops=2
    fwd = request.headers.get("x-forwarded-for")
    hops = max(1, settings.trusted_proxy_hops)
    if fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    # rejects oversized uploads before the route decodes anything
    async def dispatch(self, request, call_next):
        raw = request.headers.get("content-length")
        if raw:
            try:
                if int(raw) > settings.max_request_bytes:
                    return JSONResponse(
                        error_body(ERR.REQUEST_TOO_LARGE, 413, CODE.VALIDATION_ERROR), status_code=413
                    )
            except ValueError:
                return JSONResponse(
                    error_body(ERR.REQUEST_TOO_LARGE, 413, CODE.VALIDATION_ERROR), status_code=413
                )
        return await call_next(request)


class IPRateLimitMiddleware(BaseHTTPMiddleware):
    # coarse per-IP ceiling in front of everything, including unauthenticated 401s
    async def dispatch(self, request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        if not ip_limiter.allow(_client_ip(request)):
            log.warning("ip rate limited on %s", request.url.path)
            return JSONResponse(
                error_body(ERR.RATE_LIMITED, 429, CODE.RATE_LIMITED), status_code=429
            )
        return await call_next(request)
