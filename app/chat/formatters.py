from app.utils import es_date


def my_visitors(visitors):
    if not visitors:
        return "No tienes visitantes pendientes en este momento."
    lines = [f"{i}. **{v.get('name') or 'Sin nombre'}** — Unidad {v.get('unitNumber') or 'N/A'}" for i, v in enumerate(visitors, 1)]
    return f"Tienes **{len(visitors)}** visitante(s) pendiente(s):\n\n" + "\n".join(lines)


def _resident_lines(residents):
    out = []
    for r in residents:
        phone = f" — Tel: {r['phoneNumber']}" if r.get("phoneNumber") else ""
        out.append(f"- **{r.get('name') or 'Sin nombre'}**{phone}")
    return "\n".join(out)


def resident_lookup(units):
    if len(units) == 1:
        unit, residents = units[0]["unitNumber"], units[0]["residents"]
        if not residents:
            return f"No se encontraron residentes registrados en la unidad **{unit}**."
        return f"Residentes de la unidad **{unit}**:\n\n{_resident_lines(residents)}"

    sections = []
    for u in units:
        if not u["residents"]:
            sections.append(f"**Unidad {u['unitNumber']}:** Sin residentes registrados.")
        else:
            sections.append(f"**Unidad {u['unitNumber']}:**\n{_resident_lines(u['residents'])}")
    return "\n\n".join(sections)


def recent_visits(visits, unit_number):
    if not visits:
        return f"No se encontraron visitas recientes para la unidad **{unit_number}**."
    lines = [f"{i}. **{v.get('name') or 'Sin nombre'}** — {es_date(v.get('completedAt'))}" for i, v in enumerate(visits, 1)]
    return f"Últimas visitas completadas en la unidad **{unit_number}**:\n\n" + "\n".join(lines)


def address_book(results, term):
    if not results:
        return f'No se encontraron resultados para "{term}".'
    lines = []
    for r in results:
        unit = f" — Unidad {r['unitNumber']}" if r.get("unitNumber") else ""
        phone = f" — Tel: {r['phoneNumber']}" if r.get("phoneNumber") else ""
        lines.append(f"- **{r.get('name') or 'Sin nombre'}**{unit}{phone}")
    return f'Resultados para "{term}":\n\n' + "\n".join(lines)


def confirmation(tool_name, params):
    # text the app shows before it actually performs the write
    if tool_name == "create_visitor":
        visitors = params.get("visitors")
        if isinstance(visitors, list) and visitors:
            lines = "\n".join(f"- **{v.get('visitorName')}** en la unidad **{v.get('unitNumber')}**" for v in visitors)
            return f"Quiero registrar los siguientes visitantes:\n{lines}\n\n¿Confirmas?"
        text = f"Quiero registrar a **{params.get('visitorName')}** en la unidad **{params.get('unitNumber')}**"
        if params.get("documentId"):
            text += f" con documento **{params['documentId']}**"
        return text + ". ¿Confirmas?"

    if tool_name == "create_quick_service":
        label = "Taxi" if params.get("serviceType") == "taxi" else "Delivery"
        return f"Quiero registrar un servicio de **{label}** para tu unidad. ¿Confirmas?"

    if tool_name == "update_phone_number":
        return f"Quiero actualizar tu teléfono a **{params.get('phoneNumber')}**. ¿Confirmas?"

    return "¿Confirmas esta acción?"
