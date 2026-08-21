from openai import OpenAI

from app.config import settings

_client = None
_key = None

ID_EXTRACT_PROMPT = """You are an expert at extracting structured data from Dominican identification documents.
        Extract the following information from the provided text and return ONLY a valid JSON object with these exact keys:
        - "documentId": the identification number (digits only)
        - "name": the full name (properly capitalized)
        - "dob": the date of birth in YYYY-MM-DD format

        If any field cannot be found, use null as the value.
        Return only the JSON object, no additional text or explanation."""


def get_client(api_key: str | None = None) -> OpenAI:
    global _client, _key
    key = api_key or settings.openai_api_key
    if _client is None or _key != key:
        _client = OpenAI(api_key=key)
        _key = key
    return _client


def parse_id_text(text: str) -> str:
    # returns the raw model reply, caller json-parses it
    completion = get_client().chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": ID_EXTRACT_PROMPT},
            {"role": "user", "content": f"Extract ID, name, and date of birth from this text:\n\n{text}"},
        ],
    )
    return completion.choices[0].message.content
