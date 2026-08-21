import firebase_admin
from firebase_admin import firestore

_db = None


def get_db():
    # lazy so tests can run without creds, and so the app boots fast
    global _db
    if _db is None:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        _db = firestore.client()
    return _db
