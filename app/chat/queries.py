from google.cloud.firestore_v1 import Query
from google.cloud.firestore_v1.base_query import FieldFilter

from app.config import COLLECTIONS, settings
from app.firebase import get_db


def _docs(q):
    return [{"id": d.id, **(d.to_dict() or {})} for d in q.get()]


def my_visitors(user_id):
    q = (
        get_db().collection(COLLECTIONS.VISITORS)
        .where(filter=FieldFilter("userId", "==", user_id))
        .where(filter=FieldFilter("completed", "==", False))
        .order_by("createdAt", direction=Query.DESCENDING)
        .limit(10)
    )
    return _docs(q)


def residents_by_units(unit_numbers):
    db = get_db()
    out = []
    for unit in unit_numbers:
        q = db.collection(COLLECTIONS.AUTHORIZED_USERS).where(filter=FieldFilter("unitNumber", "==", unit))
        out.append({"unitNumber": unit, "residents": _docs(q)})
    return out


def recent_visits(unit_number):
    q = (
        get_db().collection(COLLECTIONS.VISITORS)
        .where(filter=FieldFilter("unitNumber", "==", unit_number))
        .where(filter=FieldFilter("completed", "==", True))
        .order_by("completedAt", direction=Query.DESCENDING)
        .limit(5)
    )
    return _docs(q)


def address_book(term):
    # whole collection then filter in memory — same as node, small collection
    term = (term or "").strip().lower()
    # "" is a substring of every field, so a blank term used to return the
    # whole directory — name, phone and email — in one call
    if len(term) < settings.min_search_term_chars:
        return []
    users = _docs(get_db().collection(COLLECTIONS.AUTHORIZED_USERS).order_by("name"))
    hits = [
        u for u in users
        if term in (u.get("name") or "").lower()
        or term in (u.get("phoneNumber") or "").lower()
        or term in (u.get("email") or "").lower()
    ]
    return hits[:10]
