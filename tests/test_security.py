"""Regression tests for the findings in the hardening pass.

Each test names the finding it locks down so a future refactor that reopens
the hole fails loudly rather than silently.
"""
import base64
from types import SimpleNamespace

import pytest

import app.routers.documents as documents
import app.routers.visitors as visitors
from app.chat import queries
from app.limits import RateLimiter

IMG = base64.b64encode(b"fake-image").decode()

PROTECTED = [
    ("post", "/chat", {"messages": [{"role": "user", "content": "hola"}]}),
    ("post", "/visitors", {"unitNumber": "101"}),
    ("post", "/documents/extractions", {"document": IMG, "mimeType": "image/jpeg"}),
]


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_protected_routes_reject_anonymous(client, method, path, body):
    r = getattr(client, method)(path, json=body)
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "AUTH_ERROR" and err["statusCode"] == 401


def test_profiles_requires_the_service_key(client, as_user):
    # H1 — an unauthenticated phone-number oracle over the whole directory
    assert client.get("/profiles", params={"phoneNumber": "8095551234"}).status_code == 401
    # a real resident token is not enough either; this route is n8n-only
    r = client.get("/profiles", params={"phoneNumber": "8095551234"}, headers=as_user())
    assert r.status_code == 401


def test_bad_service_key_is_rejected(client):
    r = client.get("/profiles", params={"phoneNumber": "8095551234"}, headers={"X-Service-Key": "wrong"})
    assert r.status_code == 401


def test_garbage_bearer_token_is_rejected(client, as_user):
    as_user()  # installs the fake verifier
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "x"}]},
                    headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
    assert "not-a-real-token" not in r.text


def test_authenticated_uid_without_a_profile_is_forbidden(client, as_user):
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "x"}]},
                    headers=as_user(uid="ghost", seed=False))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_deactivated_profile_is_forbidden(client, as_user):
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "x"}]},
                    headers=as_user(uid="u9", isActive=False))
    assert r.status_code == 403


# --- authorization, not just authentication ---

def test_resident_cannot_register_for_another_unit(client, as_user, monkeypatch):
    monkeypatch.setattr(visitors, "save_to_storage", lambda *a, **k: "https://storage/x.png")
    h = as_user(uid="u1", role="user", unitNumber="101")

    r = client.post("/visitors", json={"unitNumber": "999", "name": "Bob"}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"

    r = client.post("/visitors", json={"unitNumber": "101", "name": "Bob"}, headers=h)
    assert r.status_code == 201


def test_staff_may_register_for_any_unit(client, as_user, monkeypatch):
    monkeypatch.setattr(visitors, "save_to_storage", lambda *a, **k: "https://storage/x.png")
    r = client.post("/visitors", json={"unitNumber": "999", "name": "Bob"},
                    headers=as_user(uid="a1", role="admin", unitNumber=None))
    assert r.status_code == 201


def test_visitor_attribution_ignores_the_body(client, as_user, monkeypatch):
    # H2 — reportedBy/userId used to be whatever the caller typed
    monkeypatch.setattr(visitors, "save_to_storage", lambda *a, **k: "https://storage/x.png")
    h = as_user(uid="u1", role="user", unitNumber="101", name="Ana", phoneNumber="8095551234")
    d = client.post("/visitors", headers=h, json={
        "unitNumber": "101", "name": "Bob",
        "userId": "someone-else", "reportedBy": "Mallory", "reportedByNumber": "0000000000",
    }).json()["data"]

    assert d["userId"] == "u1" and d["createdBy"] == "u1"
    assert d["reportedBy"] == "Ana" and d["reportedByNumber"] == "8095551234"
    assert d["source"] == "app"


def test_service_path_keeps_whatsapp_attribution(client, service_headers, monkeypatch):
    monkeypatch.setattr(visitors, "save_to_storage", lambda *a, **k: "https://storage/x.png")
    d = client.post("/visitors", headers=service_headers, json={
        "unitNumber": "101", "name": "Bob", "userId": "u7", "reportedBy": "Ana",
    }).json()["data"]
    assert d["source"] == "whatsapp" and d["userId"] == "u7" and d["reportedBy"] == "Ana"


def test_extractions_are_staff_only(client, as_user, monkeypatch):
    # H3 — every call burns Document AI / Vision / OpenAI quota
    monkeypatch.setattr(documents.document_ai, "process_document", lambda *a, **k: pytest.fail("must not run"))
    r = client.post("/documents/extractions", json={"document": IMG, "mimeType": "image/jpeg"},
                    headers=as_user(uid="u1", role="user"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


# --- data exposure ---

def test_blank_search_term_no_longer_dumps_the_directory(db):
    # C2 — "" is a substring of every field
    for i in range(5):
        db.seed("authorizedUsers", f"u{i}", {"name": f"Resident {i}", "phoneNumber": f"80955512{i}{i}", "email": f"r{i}@x.com"})
    assert queries.address_book("") == []
    assert queries.address_book("   ") == []
    assert queries.address_book("ab") == []
    assert len(queries.address_book("Resident")) == 5


def test_internal_errors_do_not_leak_exception_text(client, as_user, monkeypatch):
    # M2 — google errors carry project ids and resource paths
    def boom(*a, **k):
        raise RuntimeError("projects/777556681966/locations/us/processors/SECRET")

    monkeypatch.setattr(documents.document_ai, "process_document", boom)
    r = client.post("/documents/extractions", json={"document": IMG, "mimeType": "image/jpeg"},
                    headers=as_user(uid="s1", role="security"))
    assert r.status_code == 500
    assert "777556681966" not in r.text and "SECRET" not in r.text


# --- transport limits ---

def test_oversized_body_is_rejected_before_the_route(client, as_user, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "max_request_bytes", 512)
    r = client.post("/chat", headers=as_user(),
                    json={"messages": [{"role": "user", "content": "x" * 4000}]})
    assert r.status_code == 413 and r.json()["success"] is False


def test_rate_limiter_refills_over_time():
    lim = RateLimiter(limit=2, window=60)
    assert lim.allow("k") and lim.allow("k")
    assert not lim.allow("k")
    assert lim.allow("other")  # buckets are per key


def test_chat_is_rate_limited_per_principal(client, as_user, monkeypatch):
    import app.chat.runner as runner
    import app.routers.chat as chat_router

    msg = SimpleNamespace(content="hola", tool_calls=None)
    completion = SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    client_stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: completion)))
    monkeypatch.setattr(runner, "get_client", lambda: client_stub)
    monkeypatch.setattr(chat_router, "chat_limiter", RateLimiter(limit=2, window=60))
    h = as_user()
    body = {"messages": [{"role": "user", "content": "hola"}]}
    assert client.post("/chat", json=body, headers=h).status_code == 200
    assert client.post("/chat", json=body, headers=h).status_code == 200
    r = client.post("/chat", json=body, headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


# --- the rate-limit key itself must not be caller-controlled ---

def _req(headers, peer="10.0.0.1"):
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))


def test_forwarded_for_is_read_from_the_trusted_end(monkeypatch):
    from app.config import settings
    from app.limits import _client_ip

    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    # a client that forges the left-hand entries must not get a fresh bucket:
    # cloud run appends the real peer last
    assert _client_ip(_req({"x-forwarded-for": "1.2.3.4, 203.0.113.9"})) == "203.0.113.9"
    assert _client_ip(_req({"x-forwarded-for": "evil, evil2, 203.0.113.9"})) == "203.0.113.9"
    assert _client_ip(_req({"x-forwarded-for": "203.0.113.9"})) == "203.0.113.9"

    # behind an external load balancer there is one more trusted hop
    monkeypatch.setattr(settings, "trusted_proxy_hops", 2)
    assert _client_ip(_req({"x-forwarded-for": "1.2.3.4, 203.0.113.9, 35.1.1.1"})) == "203.0.113.9"


def test_client_ip_falls_back_to_the_peer(monkeypatch):
    from app.limits import _client_ip
    assert _client_ip(_req({})) == "10.0.0.1"


def test_spoofed_forwarded_for_cannot_reset_the_bucket(client):
    # 130 requests from one real peer, each forging a different origin IP.
    # the trailing entry is what cloud run appends and is the only trusted one.
    codes = []
    for i in range(130):
        r = client.post("/visitors", json={"unitNumber": "1"},
                        headers={"X-Forwarded-For": f"9.9.9.{i % 256}, 203.0.113.7"})
        codes.append(r.status_code)
    assert 429 in codes, "forged X-Forwarded-For bypassed the per-IP limit"


def test_non_ascii_service_key_is_rejected_not_a_crash():
    # starlette decodes headers as latin-1, so a non-ascii key reaches us as a
    # str that secrets.compare_digest would raise TypeError on -> 500 not 401.
    # (httpx blocks this client-side, hence the direct call.)
    from app.auth import service_caller
    from app.responses import ApiError

    with pytest.raises(ApiError) as e:
        service_caller("clé-secrète")
    assert e.value.status_code == 401


def test_service_routes_fail_closed_when_the_key_is_unset(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "service_api_key", "")
    r = client.get("/profiles", params={"phoneNumber": "8095551234"},
                   headers={"X-Service-Key": "anything"})
    assert r.status_code == 503


# --- retention ---

def test_purge_is_disabled_by_default(monkeypatch):
    from app.config import settings
    from app.jobs.purge_documents import purge_documents

    monkeypatch.setattr(settings, "document_retention_days", 0)
    monkeypatch.setattr("app.jobs.purge_documents.delete_expired_documents",
                        lambda *a, **k: pytest.fail("must not delete when retention is off"))
    assert purge_documents() == 0


def test_purge_uses_the_configured_cutoff(monkeypatch):
    from app.config import settings
    from app.jobs import purge_documents as mod

    monkeypatch.setattr(settings, "document_retention_days", 30)
    seen = {}

    def fake_delete(cutoff, **k):
        seen["cutoff"] = cutoff
        return 3

    monkeypatch.setattr(mod, "delete_expired_documents", fake_delete)
    assert mod.purge_documents() == 3
    from app.utils import now_ms
    assert 0 <= now_ms() - seen["cutoff"] - 30 * mod.DAY_MS < 5000


def test_qr_url_is_stored_on_the_visitor(client, db, service_headers, monkeypatch):
    monkeypatch.setattr(visitors, "save_to_storage",
                        lambda data, name, content_type=None, **k: f"https://storage/{name}")
    d = client.post("/visitors", json={"unitNumber": "101"}, headers=service_headers).json()["data"]
    # nothing downstream should have to rebuild the object path
    assert db.collection("visitors").docs[d["id"]]["qr"] == d["qr"]
