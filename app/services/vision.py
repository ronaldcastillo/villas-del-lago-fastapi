from google.cloud import vision

_client = None


def _client_():
    global _client
    if _client is None:
        _client = vision.ImageAnnotatorClient()
    return _client


def extract_text(data: bytes) -> str:
    resp = _client_().text_detection(image=vision.Image(content=data))
    if resp.error.message:
        # vision reports per-image failures in the body instead of raising
        raise RuntimeError(resp.error.message)
    return resp.text_annotations[0].description if resp.text_annotations else ""
