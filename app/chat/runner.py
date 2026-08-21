import json
import logging

from app.chat import formatters, queries
from app.chat.prompts import SECURITY_PHONE, SECURITY_PHONE_RAW, build_system_prompt
from app.chat.tools import READ_ONLY_TOOLS, build_tools
from app.config import settings
from app.services.openai_client import get_client

log = logging.getLogger("vdl.chat")


def execute_read_only_tool(tool_name, params, ctx):
    # -> (reply, card)
    if tool_name == "get_my_visitors":
        user_id = (ctx or {}).get("userId")
        if not user_id:
            return "No se pudo identificar tu usuario.", None
        visitors = queries.my_visitors(user_id)
        card = {
            "type": "visitors_list",
            "visitors": [
                {"id": v.get("id"), "name": v.get("name"), "unitNumber": v.get("unitNumber"),
                 "createdAt": v.get("createdAt"), "reportedBy": v.get("reportedBy")}
                for v in visitors
            ],
        } if visitors else None
        return formatters.my_visitors(visitors), card

    if tool_name == "lookup_resident":
        units = params.get("unitNumbers")
        units = units if isinstance(units, list) else [units]
        result = queries.residents_by_units(units)
        any_residents = any(u["residents"] for u in result)
        card = {
            "type": "residents_list",
            "units": [
                {"unitNumber": u["unitNumber"],
                 "residents": [{"name": r.get("name"), "phoneNumber": r.get("phoneNumber"), "email": r.get("email")} for r in u["residents"]]}
                for u in result
            ],
        } if any_residents else None
        return formatters.resident_lookup(result), card

    if tool_name == "get_recent_visits":
        unit = params.get("unitNumber")
        return formatters.recent_visits(queries.recent_visits(unit), unit), None

    if tool_name == "search_address_book":
        term = params.get("searchTerm")
        return formatters.address_book(queries.address_book(term), term), None

    if tool_name == "show_security_phone":
        return (
            f"El teléfono de seguridad es **{SECURITY_PHONE}**.",
            {"type": "phone", "label": "Seguridad", "phone": SECURITY_PHONE_RAW, "display": SECURITY_PHONE},
        )

    return "Herramienta no reconocida.", None


def run_chat(messages, ctx):
    # -> {reply, action, card}
    ctx = ctx or {}
    history = messages[-settings.chat_max_history:]

    completion = get_client().chat.completions.create(
        model=settings.chat_model,
        messages=[{"role": "system", "content": build_system_prompt(ctx)}, *history],
        tools=build_tools(ctx.get("role")),
        max_completion_tokens=settings.chat_max_tokens,
    )
    msg = completion.choices[0].message
    calls = [c for c in (msg.tool_calls or []) if getattr(c, "type", "function") == "function"]

    if not calls:
        return {"reply": msg.content, "action": None, "card": None}

    # only the first tool call counts, same as before
    call = calls[0]
    tool_name = call.function.name
    try:
        params = json.loads(call.function.arguments or "{}")
    except Exception:
        log.warning("chat: bad tool args for %s", tool_name)
        params = {}
    if not isinstance(params, dict):
        params = {}

    if tool_name in READ_ONLY_TOOLS:
        reply, card = execute_read_only_tool(tool_name, params, ctx)
        return {"reply": reply, "action": None, "card": card}

    reply = formatters.confirmation(tool_name, params)

    # multi-visitor gets its own action type
    visitors = params.get("visitors")
    if tool_name == "create_visitor" and isinstance(visitors, list) and visitors:
        return {"reply": reply, "action": {"type": "create_visitors", "params": {"visitors": visitors}}, "card": None}

    return {"reply": reply, "action": {"type": tool_name, "params": params}, "card": None}
