import os

# no creds, no background stuff in tests
SERVICE_KEY = "test-service-key"
os.environ.update({
    "ENABLE_LISTENERS": "false",
    "ENABLE_SCHEDULER": "false",
    "OPENAI_API_KEY": "test-key",
    "SERVICE_API_KEY": SERVICE_KEY,
})

import pytest
from fastapi.testclient import TestClient

import app.firebase as firebase_mod
from tests.fake_firestore import FakeDB


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    # the limiters are module-level singletons; don't let one test starve the next
    from app import limits
    for lim in (limits.ip_limiter, limits.chat_limiter, limits.extraction_limiter):
        lim._buckets.clear()
    yield


@pytest.fixture
def db():
    fake = FakeDB()
    firebase_mod._db = fake
    yield fake
    firebase_mod._db = None


@pytest.fixture
def client(db):
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def as_user(db, monkeypatch):
    """Seed an authorizedUsers profile and return headers that authenticate as it."""
    import app.auth as auth_mod

    issued = {}

    def fake_verify(token, *a, **k):
        if token not in issued:
            raise ValueError("invalid token")
        return {"uid": issued[token]}

    monkeypatch.setattr(auth_mod.fb_auth, "verify_id_token", fake_verify)

    def make(uid="u1", role="user", unitNumber="101", name="Ana",
             phoneNumber="8095551234", isActive=True, seed=True, **extra):
        if seed:
            db.seed("authorizedUsers", uid, {
                "name": name, "role": role, "unitNumber": unitNumber,
                "phoneNumber": phoneNumber, "isActive": isActive, **extra,
            })
        token = f"tok-{uid}"
        issued[token] = uid
        return {"Authorization": f"Bearer {token}"}

    return make


@pytest.fixture
def service_headers():
    return {"X-Service-Key": SERVICE_KEY}
