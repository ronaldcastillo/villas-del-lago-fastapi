from app.jobs.expire_visits import expire_visits
from app.utils import now_ms


def test_expire_visits(db):
    now = now_ms()
    db.seed("visitors", "late", {"completed": False, "expiresAt": now - 1000})
    db.seed("visitors", "fresh", {"completed": False, "expiresAt": now + 100000})
    db.seed("visitors", "done", {"completed": True, "expiresAt": now - 1000})

    assert expire_visits() == 1
    late = db.collection("visitors").docs["late"]
    assert late["completed"] is True and late["expired"] is True and late["expiredAt"] == late["updatedAt"]
    assert db.collection("visitors").docs["fresh"]["completed"] is False
    assert "expired" not in db.collection("visitors").docs["done"]


def test_expire_visits_nothing(db):
    assert expire_visits() == 0
