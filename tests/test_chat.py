import json
from types import SimpleNamespace

import pytest

import app.chat.runner as runner
from app.chat import formatters
from app.chat.prompts import build_system_prompt
from app.chat.tools import build_tools


def _names(tools):
    return [t["function"]["name"] for t in tools]


def test_tools_by_role():
    assert _names(build_tools("user")) == ["create_visitor", "show_security_phone", "update_phone_number", "get_my_visitors", "create_quick_service"]
    assert _names(build_tools("security")) == ["show_security_phone", "lookup_resident", "get_recent_visits", "search_address_book"]
    admin = _names(build_tools("admin"))
    assert admin[0] == "create_visitor" and {"lookup_resident", "get_my_visitors", "update_phone_number"} <= set(admin)
    assert _names(build_tools(None)) == ["show_security_phone"]


def test_system_prompt():
    p = build_system_prompt({"role": "user", "name": "Ana", "unitNumber": "101"})
    assert "- Nombre: Ana" in p and "- Rol: Residente" in p and "create_quick_service" in p
    p = build_system_prompt({"role": "security"})
    assert "Rol: Seguridad" in p and "exclusiva para residentes" in p
    assert "Rol: Usuario" in build_system_prompt(None)


def test_formatters():
    assert formatters.my_visitors([]) == "No tienes visitantes pendientes en este momento."
    assert formatters.my_visitors([{"name": "Ana", "unitNumber": "101"}]) == "Tienes **1** visitante(s) pendiente(s):\n\n1. **Ana** — Unidad 101"
    units = [{"unitNumber": "101", "residents": [{"name": "Ana", "phoneNumber": "809"}]}, {"unitNumber": "102", "residents": []}]
    assert formatters.resident_lookup(units) == "**Unidad 101:**\n- **Ana** — Tel: 809\n\n**Unidad 102:** Sin residentes registrados."
    assert formatters.resident_lookup(units[1:]) == "No se encontraron residentes registrados en la unidad **102**."
    assert formatters.confirmation("create_visitor", {"visitorName": "Ana", "unitNumber": "101", "documentId": "1"}) == \
        "Quiero registrar a **Ana** en la unidad **101** con documento **1**. ¿Confirmas?"
    assert formatters.confirmation("create_quick_service", {"serviceType": "taxi"}) == "Quiero registrar un servicio de **Taxi** para tu unidad. ¿Confirmas?"
    assert formatters.confirmation("whatever", {}) == "¿Confirmas esta acción?"


def _completion(content=None, tool=None, args=None):
    calls = None
    if tool:
        calls = [SimpleNamespace(type="function", function=SimpleNamespace(name=tool, arguments=args if isinstance(args, str) else json.dumps(args or {})))]
    msg = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def fake_openai(monkeypatch):
    state = {"completion": _completion("hola"), "kwargs": None}

    def create(**kwargs):
        state["kwargs"] = kwargs
        return state["completion"]

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(runner, "get_client", lambda: client)
    return state


def test_plain_reply(db, fake_openai):
    out = runner.run_chat([{"role": "user", "content": "hola"}], {"role": "user"})
    assert out == {"reply": "hola", "action": None, "card": None}
    sent = fake_openai["kwargs"]
    assert sent["messages"][0]["role"] == "system" and sent["max_completion_tokens"] == 1000


def test_history_trimmed(db, fake_openai):
    msgs = [{"role": "user", "content": str(i)} for i in range(15)]
    runner.run_chat(msgs, {})
    assert len(fake_openai["kwargs"]["messages"]) == 11  # system + last 10


def test_lookup_resident_runs_on_backend(db, fake_openai):
    db.seed("authorizedUsers", "u1", {"name": "Ana", "unitNumber": "101", "phoneNumber": "809", "email": "a@x"})
    fake_openai["completion"] = _completion(tool="lookup_resident", args={"unitNumbers": ["101", "102"]})
    out = runner.run_chat([{"role": "user", "content": "101 y 102"}], {"role": "security"})
    assert out["action"] is None
    assert out["card"] == {"type": "residents_list", "units": [
        {"unitNumber": "101", "residents": [{"name": "Ana", "phoneNumber": "809", "email": "a@x"}]},
        {"unitNumber": "102", "residents": []},
    ]}
    assert out["reply"].startswith("**Unidad 101:**")


def test_lookup_resident_scalar_arg(db, fake_openai):
    fake_openai["completion"] = _completion(tool="lookup_resident", args={"unitNumbers": "101"})
    out = runner.run_chat([{"role": "user", "content": "101"}], {"role": "security"})
    assert out["reply"] == "No se encontraron residentes registrados en la unidad **101**."


def test_get_my_visitors(db, fake_openai):
    db.seed("visitors", "v1", {"userId": "u1", "completed": False, "name": "Ana", "unitNumber": "101", "createdAt": 2, "reportedBy": "me"})
    db.seed("visitors", "v2", {"userId": "u1", "completed": True, "name": "Old", "unitNumber": "101", "createdAt": 1})
    fake_openai["completion"] = _completion(tool="get_my_visitors")
    out = runner.run_chat([{"role": "user", "content": "mis visitas"}], {"role": "user", "userId": "u1"})
    assert out["card"]["type"] == "visitors_list" and [v["id"] for v in out["card"]["visitors"]] == ["v1"]

    out = runner.run_chat([{"role": "user", "content": "mis visitas"}], {"role": "user"})
    assert out["reply"] == "No se pudo identificar tu usuario."


def test_security_phone(db, fake_openai):
    fake_openai["completion"] = _completion(tool="show_security_phone")
    out = runner.run_chat([{"role": "user", "content": "tel seguridad"}], {})
    assert out["card"]["phone"] == "8295449011" and "(829) 544-9011" in out["reply"]


def test_write_tool_returns_action(db, fake_openai):
    fake_openai["completion"] = _completion(tool="create_visitor", args={"visitorName": "Ana", "unitNumber": "101"})
    out = runner.run_chat([{"role": "user", "content": "registra a ana"}], {"role": "user"})
    assert out["action"] == {"type": "create_visitor", "params": {"visitorName": "Ana", "unitNumber": "101"}}
    assert out["reply"] == "Quiero registrar a **Ana** en la unidad **101**. ¿Confirmas?"


def test_multi_visitor_action(db, fake_openai):
    vs = [{"visitorName": "Ana", "unitNumber": "101"}, {"visitorName": "Bob", "unitNumber": "101"}]
    fake_openai["completion"] = _completion(tool="create_visitor", args={"visitors": vs})
    out = runner.run_chat([{"role": "user", "content": "ana y bob"}], {"role": "user"})
    assert out["action"] == {"type": "create_visitors", "params": {"visitors": vs}}


def test_bad_tool_args(db, fake_openai):
    fake_openai["completion"] = _completion(tool="update_phone_number", args="{not json")
    out = runner.run_chat([{"role": "user", "content": "x"}], {"role": "user"})
    assert out["action"] == {"type": "update_phone_number", "params": {}}


def test_chat_endpoint_validation(client, as_user):
    h = as_user()
    r = client.post("/chat", json={"messages": []}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.post("/chat", json={"messages": [{"role": "system", "content": "x"}]}, headers=h)
    assert r.status_code == 400
    r = client.post("/chat", json={"messages": [{"role": "user", "content": 5}]}, headers=h)
    assert r.status_code == 400


def test_chat_endpoint_ok(client, fake_openai, as_user):
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hola"}]}, headers=as_user())
    assert r.status_code == 200 and r.json()["data"] == {"reply": "hola", "action": None, "card": None}


def test_chat_context_comes_from_the_token_not_the_body(client, fake_openai, as_user):
    # the whole C1 hole: claiming admin in the body used to hand out admin tools
    h = as_user(uid="u1", role="user", name="Ana", unitNumber="101")
    r = client.post("/chat", headers=h, json={
        "messages": [{"role": "user", "content": "hola"}],
        "userContext": {"role": "admin", "userId": "someone-else", "name": "Mallory"},
    })
    assert r.status_code == 200

    sent = fake_openai["kwargs"]
    names = [t["function"]["name"] for t in sent["tools"]]
    assert "lookup_resident" not in names and "search_address_book" not in names
    assert names == _names(build_tools("user")) and "- Nombre: Ana" in sent["messages"][0]["content"]
    assert "Mallory" not in sent["messages"][0]["content"]


def test_chat_requires_auth(client):
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hola"}]})
    assert r.status_code == 401
    body = r.json()
    assert body["success"] is False and body["error"]["code"] == "AUTH_ERROR"


def test_chat_rejects_oversized_message(client, as_user):
    r = client.post("/chat", headers=as_user(),
                    json={"messages": [{"role": "user", "content": "x" * 5000}]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
