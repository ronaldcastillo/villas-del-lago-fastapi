import base64
import re
from types import SimpleNamespace

import openai
import pytest
from google.api_core.exceptions import PermissionDenied

import app.routers.documents as documents
import app.routers.visitors as visitors

IMG = base64.b64encode(b"fake-image").decode()


@pytest.fixture
def staff(as_user):
    return as_user(uid="sec1", role="security", name="Guard")


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_cors_is_not_wildcard(client):
    # no CORS_ORIGINS configured -> no origin is allowed at all
    r = client.options("/visitors", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") != "*"


def test_create_visitor(client, db, monkeypatch, service_headers):
    monkeypatch.setattr(visitors, "save_to_storage", lambda data, name, content_type=None, **k: f"https://storage/{name}")
    r = client.post("/visitors", json={"unitNumber": "101", "name": "Ana", "userId": "u1"}, headers=service_headers)
    body = r.json()
    assert r.status_code == 201 and body["success"] is True
    d = body["data"]
    assert d["unitNumber"] == "101" and d["completed"] is False and d["source"] == "whatsapp"
    assert d["expiresAt"] == d["createdAt"] + 24 * 3600 * 1000
    assert d["createdBy"] == "u1" and d["qr"] == f"https://storage/{d['id']}.png"
    assert db.collection("visitors").docs[d["id"]]["name"] == "Ana"


def test_create_visitor_missing_unit(client, service_headers):
    r = client.post("/visitors", json={"name": "Ana"}, headers=service_headers)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["message"] == "Unit number is required" and err["code"] == "VALIDATION_ERROR" and err["statusCode"] == 400


def test_validation_error_envelope(client, service_headers):
    # pydantic rejections still come back in the node-style envelope
    r = client.post("/visitors", json={"unitNumber": 5}, headers=service_headers)
    assert r.status_code == 400 and r.json()["success"] is False


def test_profile_by_phone(client, db, service_headers):
    db.seed("authorizedUsers", "u1", {"name": "Ana", "unitNumber": "101", "phoneNumber": "8095551234", "isActive": True, "role": "user"})
    r = client.get("/profiles", params={"phoneNumber": "whatsapp:+18095551234"}, headers=service_headers)
    assert r.json()["data"] == {"id": "u1", "name": "Ana", "unitNumber": "101", "isActive": True, "role": "user"}

    r = client.get("/profiles", params={"phoneNumber": "0000000000"}, headers=service_headers)
    assert r.status_code == 404 and r.json()["error"]["code"] == "VALIDATION_ERROR"

    r = client.get("/profiles", headers=service_headers)
    assert r.status_code == 400 and r.json()["error"]["message"] == "Phone Number was not provided"


def _entity(type_, text, normalized=None):
    return SimpleNamespace(type_=type_, mention_text=text, normalized_value=SimpleNamespace(text=normalized or ""))


def test_document_ai(client, monkeypatch, staff):
    result = SimpleNamespace(document=SimpleNamespace(entities=[
        _entity("ID", "001-1234567-8"), _entity("Name", "JUAN PEREZ"), _entity("DOB", "1/2/1990", "1990-02-01"),
    ]))
    monkeypatch.setattr(documents.document_ai, "process_document", lambda data, mime: result)
    monkeypatch.setattr(documents, "save_to_storage", lambda data, name, content_type=None, **k: f"https://storage/{name}")
    r = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/jpeg"})
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["documentId"] == "00112345678" and d["name"] == "Juan Perez" and d["dob"] == "1990-02-01"
    # the national id must not appear anywhere in the stored object name
    assert "00112345678" not in d["documentUrl"] and d["documentUrl"].endswith(".jpg")
    assert re.fullmatch(r"https://storage/[0-9a-f]{32}\.jpg", d["documentUrl"])


def test_document_ai_storage_failure_is_swallowed(client, monkeypatch, staff):
    result = SimpleNamespace(document=SimpleNamespace(entities=[_entity("ID", "123")]))
    monkeypatch.setattr(documents.document_ai, "process_document", lambda data, mime: result)

    def boom(*a, **k):
        raise RuntimeError("bucket down")
    monkeypatch.setattr(documents, "save_to_storage", boom)
    d = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/jpeg"}).json()["data"]
    assert d["documentId"] == "123" and d["documentUrl"] is None


def test_document_ai_permission(client, monkeypatch, staff):
    def denied(*a, **k):
        raise PermissionDenied("nope")
    monkeypatch.setattr(documents.document_ai, "process_document", denied)
    r = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/jpeg"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "AUTH_ERROR"


def test_document_validation(client, staff):
    r = client.post("/documents/extractions", headers=staff, json={"mimeType": "image/jpeg"})
    assert r.status_code == 400 and r.json()["error"]["message"] == "Document in base64 format is required"
    r = client.post("/documents/extractions", headers=staff, json={"document": IMG})
    assert r.json()["error"]["message"] == "MIME type is required"


def test_vision_and_ai(client, monkeypatch, staff):
    monkeypatch.setattr(documents.vision, "extract_text", lambda data: "CEDULA 001-1234567-8 JUAN PEREZ")
    monkeypatch.setattr(documents, "parse_id_text", lambda text: '{"id": "001-1234567-8", "name": "Juan Perez", "dob": "1990-02-01"}')
    monkeypatch.setattr(documents, "save_to_storage", lambda data, name, content_type=None, **k: "https://storage/x")
    d = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/png", "engine": "vision-ai"}).json()["data"]
    assert d == {"documentId": "00112345678", "name": "Juan Perez", "dob": "1990-02-01", "documentUrl": "https://storage/x"}


def test_vision_no_text(client, monkeypatch, staff):
    monkeypatch.setattr(documents.vision, "extract_text", lambda data: "")
    r = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/png", "engine": "vision-ai"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "NO_TEXT_ERROR"


def test_vision_bad_json_from_model(client, monkeypatch, staff):
    monkeypatch.setattr(documents.vision, "extract_text", lambda data: "some text")
    monkeypatch.setattr(documents, "parse_id_text", lambda text: "not json")
    d = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/png", "engine": "vision-ai"}).json()["data"]
    assert d == {"documentId": None, "name": None, "dob": None}


def test_vision_openai_error(client, monkeypatch, staff):
    monkeypatch.setattr(documents.vision, "extract_text", lambda data: "some text")

    def boom(text):
        raise openai.OpenAIError("rate limited")
    monkeypatch.setattr(documents, "parse_id_text", boom)
    r = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/png", "engine": "vision-ai"})
    assert r.status_code == 400 and r.json()["error"] | {"message": "OpenAI API error", "code": "OPENAI_ERROR"} == r.json()["error"]


def test_vision_no_key(client, monkeypatch, staff):
    monkeypatch.setattr(documents.settings, "openai_api_key", "")
    r = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/png", "engine": "vision-ai"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "CONFIG_ERROR"


def test_unknown_engine(client, staff):
    r = client.post("/documents/extractions", headers=staff, json={"document": IMG, "mimeType": "image/png", "engine": "magic"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
