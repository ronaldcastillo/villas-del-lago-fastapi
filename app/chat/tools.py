# tool schemas for openai — backend runs the read-only ones, the app executes the writes after confirming
READ_ONLY_TOOLS = {"get_my_visitors", "lookup_resident", "get_recent_visits", "search_address_book", "show_security_phone"}


def _fn(name, description, properties=None, required=None):
    params = {"type": "object", "properties": properties or {}}
    if required:
        params["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


def build_tools(role):
    tools = [_fn("show_security_phone", "Muestra el teléfono de seguridad del residencial con una tarjeta interactiva")]

    if role in ("user", "admin"):
        tools.append(_fn(
            "update_phone_number",
            "Actualiza el número de teléfono del usuario en el sistema",
            {"phoneNumber": {"type": "string", "description": "El nuevo número de teléfono del usuario"}},
            ["phoneNumber"],
        ))
        # create_visitor goes first, matching the original ordering
        tools.insert(0, _fn(
            "create_visitor",
            "Registra uno o varios visitantes esperados",
            {
                "visitorName": {"type": "string", "description": "Nombre completo del visitante (para un solo visitante)"},
                "unitNumber": {"type": "string", "description": "Número de la unidad a visitar"},
                "documentId": {"type": "string", "description": "Número de documento de identidad del visitante (opcional)"},
                "visitors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "visitorName": {"type": "string", "description": "Nombre completo"},
                            "unitNumber": {"type": "string", "description": "Número de unidad"},
                            "documentId": {"type": "string", "description": "Documento (opcional)"},
                        },
                        "required": ["visitorName", "unitNumber"],
                    },
                    "description": "Lista de visitantes cuando se registran varios a la vez",
                },
            },
        ))
        tools.append(_fn("get_my_visitors", "Consulta los visitantes pendientes del usuario actual"))
        tools.append(_fn(
            "create_quick_service",
            "Registra un servicio rápido de taxi o delivery para la unidad del usuario. Úsalo para delivery cuando el usuario mencione haber pedido comida, un delivery, una aplicación de entrega (PedidosYa, Uber Eats, etc.), o una orden a un colmado, farmacia, restaurante u otro negocio local. Úsalo para taxi cuando el usuario mencione que viene un Uber, taxi, o vehículo a recoger a alguien de su casa.",
            {"serviceType": {"type": "string", "enum": ["taxi", "delivery"], "description": "Tipo de servicio: taxi o delivery"}},
            ["serviceType"],
        ))

    if role in ("security", "admin"):
        tools.append(_fn(
            "lookup_resident",
            "Busca qué residentes viven en una o varias unidades. Siempre pasa todas las unidades pedidas en una sola llamada.",
            {"unitNumbers": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Lista de números de unidad a consultar (uno o varios)"}},
            ["unitNumbers"],
        ))
        tools.append(_fn(
            "get_recent_visits",
            "Consulta las visitas completadas recientemente para una unidad",
            {"unitNumber": {"type": "string", "description": "Número de la unidad a consultar"}},
            ["unitNumber"],
        ))
        tools.append(_fn(
            "search_address_book",
            "Busca un residente por nombre, teléfono o email en el directorio",
            {"searchTerm": {"type": "string", "description": "Término de búsqueda (nombre, teléfono o email)"}},
            ["searchTerm"],
        ))

    return tools
