from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.jobs.expire_visits import expire_visits
from app.jobs.purge_documents import purge_documents
from app.listeners import manager

scheduler = BackgroundScheduler()


def start(with_watchdog=True):
    scheduler.add_job(
        expire_visits,
        IntervalTrigger(minutes=settings.expire_interval_minutes),
        id="expire_visits",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    if settings.document_retention_days > 0:
        scheduler.add_job(
            purge_documents,
            IntervalTrigger(hours=settings.purge_interval_hours),
            id="purge_documents",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    if with_watchdog:
        scheduler.add_job(manager.check, IntervalTrigger(seconds=60), id="listener_watchdog", max_instances=1, coalesce=True)
    scheduler.start()


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)
