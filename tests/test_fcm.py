from types import SimpleNamespace

from firebase_admin import exceptions as fb_exc
from firebase_admin import messaging

import app.services.fcm as fcm


def _resp(ok=True, exc=None):
    return SimpleNamespace(success=ok, exception=exc)


def test_send_prunes_dead_tokens(db, monkeypatch):
    r1 = db.seed("fcmTokens", "t1", {"token": "t1", "userId": "u1"})
    r2 = db.seed("fcmTokens", "t2", {"token": "t2", "userId": "u2"})
    r3 = db.seed("fcmTokens", "t3", {"token": "t3", "userId": "u3"})
    r4 = db.seed("fcmTokens", "t4", {"token": "t4", "userId": "u4"})
    sent = []

    def fake_send(msg):
        sent.append(msg)
        return SimpleNamespace(success_count=1, failure_count=3, responses=[
            _resp(),
            _resp(False, messaging.UnregisteredError("gone")),
            _resp(False, fb_exc.InvalidArgumentError("The registration token is not a valid FCM registration token")),
            _resp(False, fb_exc.InvalidArgumentError("bad payload")),  # not token related, keep it
        ])
    monkeypatch.setattr(fcm.messaging, "send_each_for_multicast", fake_send)

    ok, failed = fcm.send_to_tokens([(r1, "t1"), (r2, "t2"), (r3, "t3"), (r4, "t4")], "hi", "there", data={"a": "b"})
    assert (ok, failed) == (1, 3)
    assert set(db.collection("fcmTokens").docs) == {"t1", "t4"}
    m = sent[0]
    assert m.fids == ["t1", "t2", "t3", "t4"] and m.notification.title == "hi" and m.data == {"a": "b"}
    assert m.webpush.fcm_options.link == "/"


def test_send_chunks_by_500(db, monkeypatch):
    calls = []

    def fake_send(msg):
        calls.append(len(msg.fids))
        return SimpleNamespace(success_count=len(msg.fids), failure_count=0, responses=[_resp()] * len(msg.fids))
    monkeypatch.setattr(fcm.messaging, "send_each_for_multicast", fake_send)
    entries = [(None, f"t{i}") for i in range(1001)]
    assert fcm.send_to_tokens(entries, "a", "b") == (1001, 0)
    assert calls == [500, 500, 1]


def test_tokens_for_users(db):
    db.seed("fcmTokens", "tok-a", {"token": "tok-a", "userId": "u1"})
    db.seed("fcmTokens", "legacy", {"token": "tok-b", "userId": "u2"})
    db.seed("fcmTokens", "other", {"token": "tok-c", "userId": "u99"})
    got = fcm.tokens_for_users([f"u{i}" for i in range(1, 40)])  # >30 forces chunking
    assert sorted(t for _, t in got) == ["tok-a", "tok-b"]


def test_all_tokens_prefers_doc_id(db):
    db.seed("fcmTokens", "abc", {"userId": "u1"})
    assert [t for _, t in fcm.all_tokens()] == ["abc"]
