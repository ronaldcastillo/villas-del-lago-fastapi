from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from google.cloud.firestore_v1.watch import ChangeType

import app.listeners.announcement as announcement
import app.listeners.visitors as visitors_mod
from app.listeners.visitors import VisitorWatcher

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _snap(doc_id, data, created=None):
    return SimpleNamespace(id=doc_id, to_dict=lambda: data, create_time=created or T0 + timedelta(seconds=5))


def _change(type_, snap):
    return SimpleNamespace(type=type_, document=snap)


def _watcher(monkeypatch):
    calls = {"security": [], "residents": []}
    monkeypatch.setattr(visitors_mod, "notify_security", lambda d: calls["security"].append(d))
    monkeypatch.setattr(visitors_mod, "notify_residents", lambda d: calls["residents"].append(d))
    w = VisitorWatcher()
    existing = [_snap("old1", {"completed": False, "unitNumber": "101"}, T0 - timedelta(days=1))]
    w(existing, [_change(ChangeType.ADDED, s) for s in existing], T0)  # initial snapshot
    return w, calls


def test_initial_snapshot_is_silent(monkeypatch):
    w, calls = _watcher(monkeypatch)
    assert calls == {"security": [], "residents": []} and w.completed == {"old1": False}


def test_new_visitor_pings_security(monkeypatch):
    w, calls = _watcher(monkeypatch)
    new = _snap("v1", {"completed": False, "unitNumber": "102", "name": "Ana"})
    w([], [_change(ChangeType.ADDED, new)], T0)
    assert calls["security"] == [{"completed": False, "unitNumber": "102", "name": "Ana"}]
    assert calls["residents"] == []


def test_doc_sliding_into_window_is_not_new(monkeypatch):
    w, calls = _watcher(monkeypatch)
    stale = _snap("ancient", {"completed": False, "unitNumber": "1"}, T0 - timedelta(days=30))
    w([], [_change(ChangeType.ADDED, stale)], T0)
    assert calls["security"] == []


def test_completion_pings_residents(monkeypatch):
    w, calls = _watcher(monkeypatch)
    done = _snap("old1", {"completed": True, "unitNumber": "101", "name": "Ana"})
    w([], [_change(ChangeType.MODIFIED, done)], T0)
    assert calls["residents"] == [{"completed": True, "unitNumber": "101", "name": "Ana"}]
    # second modify while already completed -> nothing
    w([], [_change(ChangeType.MODIFIED, done)], T0)
    assert len(calls["residents"]) == 1


def test_expired_completion_is_ignored(monkeypatch):
    w, calls = _watcher(monkeypatch)
    w([], [_change(ChangeType.MODIFIED, _snap("old1", {"completed": True, "expired": True, "expiredAt": 1, "unitNumber": "101"}))], T0)
    assert calls["residents"] == []


def test_removed_drops_state(monkeypatch):
    w, _ = _watcher(monkeypatch)
    w([], [_change(ChangeType.REMOVED, _snap("old1", {}))], T0)
    assert "old1" not in w.completed


def test_callback_swallows_errors(monkeypatch):
    w, _ = _watcher(monkeypatch)
    w([], [SimpleNamespace(type=ChangeType.ADDED, document=None)], T0)  # would blow up, must not raise


def test_notify_security_targets_active_security(db, monkeypatch):
    db.seed("authorizedUsers", "s1", {"role": "security", "isActive": True})
    db.seed("authorizedUsers", "s2", {"role": "security", "isActive": False})
    db.seed("authorizedUsers", "r1", {"role": "user", "isActive": True, "unitNumber": "101"})
    db.seed("fcmTokens", "tok-s1", {"token": "tok-s1", "userId": "s1"})
    db.seed("fcmTokens", "tok-s2", {"token": "tok-s2", "userId": "s2"})
    db.seed("fcmTokens", "tok-r1", {"token": "tok-r1", "userId": "r1"})
    sent = []
    monkeypatch.setattr(visitors_mod, "send_to_tokens", lambda entries, title, body, **k: sent.append(([t for _, t in entries], title, body)))

    visitors_mod.notify_security({"unitNumber": "101"})
    assert sent == [(["tok-s1"], "Nueva Visita", "Un visitante - Unidad 101")]

    visitors_mod.notify_residents({"unitNumber": "101", "name": "Ana"})
    assert sent[-1] == (["tok-r1"], "Tu visitante ha llegado", "Ana ha llegado al residencial")

    visitors_mod.notify_residents({"name": "no unit"})
    assert len(sent) == 2


def _announcement_snap(db, data):
    ref = db.seed("siteConfig", "announcement", data)
    return ref.get()


def test_announcement_broadcast(db, monkeypatch):
    db.seed("fcmTokens", "tok1", {"userId": "u1"})
    sent = []
    monkeypatch.setattr(announcement, "send_to_tokens", lambda entries, title, body, data=None, **k: sent.append((title, body, data)))

    announcement.broadcast(_announcement_snap(db, {"enabled": True, "message": "  Se va la luz  ", "updatedAt": 123}))
    assert sent == [("Anuncio", "Se va la luz", {"type": "announcement", "updatedAt": "123"})]
    assert db.collection("siteConfig").docs["announcement"]["lastNotifiedAt"] == 123

    # same version again -> no push
    announcement.broadcast(_announcement_snap(db, {"enabled": True, "message": "x", "updatedAt": 123, "lastNotifiedAt": 123}))
    assert len(sent) == 1


def test_announcement_guards(db, monkeypatch):
    sent = []
    monkeypatch.setattr(announcement, "send_to_tokens", lambda *a, **k: sent.append(1))
    for data in (
        {"enabled": True, "message": "x"},                      # no updatedAt
        {"enabled": True, "message": "x", "updatedAt": True},   # bool is not a number
        {"enabled": False, "message": "x", "updatedAt": 1},
        {"enabled": True, "message": "   ", "updatedAt": 1},
    ):
        announcement.broadcast(_announcement_snap(db, data))
    assert sent == []


def test_announcement_no_tokens_still_marks_notified(db, monkeypatch):
    monkeypatch.setattr(announcement, "send_to_tokens", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not send")))
    announcement.broadcast(_announcement_snap(db, {"enabled": True, "message": "x", "title": " Hey ", "updatedAt": 9}))
    assert db.collection("siteConfig").docs["announcement"]["lastNotifiedAt"] == 9


def test_truncate():
    assert announcement._truncate("a" * 3500) == "a" * 3500
    assert announcement._truncate("a" * 3600) == "a" * 3499 + "…"
