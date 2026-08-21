import logging

from app.listeners import announcement, visitors

log = logging.getLogger("vdl.listeners")

FACTORIES = {"visitors": visitors.start, "announcement": announcement.start}
_watches = {}


def start_all():
    for name, factory in FACTORIES.items():
        _watches[name] = factory()
        log.info("listener %s started", name)


def stop_all():
    for name, w in list(_watches.items()):
        try:
            w.unsubscribe()
        except Exception:
            log.warning("listener %s failed to stop", name, exc_info=True)
    _watches.clear()


def _dead(w):
    # sdk closes the watch on non-retryable rpc errors and never reopens it
    return getattr(w, "_closed", False) or not w.is_active


def check():
    # watchdog, runs on the scheduler
    for name, w in list(_watches.items()):
        if not _dead(w):
            continue
        log.warning("listener %s died, restarting", name)
        try:
            w.close()
        except Exception:
            pass
        try:
            _watches[name] = FACTORIES[name]()
        except Exception:
            log.exception("listener %s restart failed", name)
