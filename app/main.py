import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.limits import BodySizeLimitMiddleware, IPRateLimitMiddleware
from app.responses import install_handlers
from app.routers import chat, documents, profiles, visitors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vdl")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # listeners + cron live in-process; run exactly one worker
    if settings.enable_listeners:
        from app.listeners import manager
        manager.start_all()
    if settings.enable_scheduler:
        from app.jobs import scheduler
        scheduler.start(with_watchdog=settings.enable_listeners)
    yield
    if settings.enable_scheduler:
        from app.jobs import scheduler
        scheduler.stop()
    if settings.enable_listeners:
        from app.listeners import manager
        manager.stop_all()


def create_app() -> FastAPI:
    app = FastAPI(title="Villas del Lago API", lifespan=lifespan)
    # middleware runs outermost-last, so add the cheap guards after CORS
    origins = settings.cors_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Service-Key"],
        )
    else:
        # the mobile app and n8n are not browsers — no origin needs to be allowed.
        # set CORS_ORIGINS only if a web client is added.
        log.info("CORS disabled (no CORS_ORIGINS configured)")
    app.add_middleware(IPRateLimitMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    install_handlers(app)
    for r in (visitors.router, profiles.router, documents.router, chat.router):
        app.include_router(r)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


app = create_app()
