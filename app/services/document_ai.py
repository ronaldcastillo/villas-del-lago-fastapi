from google.api_core.client_options import ClientOptions
from google.cloud import documentai

from app.config import settings
from app.utils import format_name, sanitize_document_id

_client = None


def _client_():
    global _client
    if _client is None:
        loc = settings.document_ai_location
        _client = documentai.DocumentProcessorServiceClient(
            client_options=ClientOptions(api_endpoint=f"{loc}-documentai.googleapis.com")
        )
    return _client


def process_document(data: bytes, mime_type: str):
    client = _client_()
    name = client.processor_path(settings.project_id, settings.document_ai_location, settings.document_ai_processor_id)
    return client.process_document(request={"name": name, "raw_document": {"content": data, "mime_type": mime_type}})


def extract_entities(result) -> dict:
    out = {"documentId": None, "name": None, "dob": None}
    for e in getattr(getattr(result, "document", None), "entities", []) or []:
        if e.type_ == "DOB":
            out["dob"] = (e.normalized_value.text if e.normalized_value else "") or e.mention_text
        elif e.type_ == "Name":
            out["name"] = format_name(e.mention_text)
        elif e.type_ == "ID":
            out["documentId"] = sanitize_document_id(e.mention_text)
    return out
