from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    project_id: str = "777556681966"
    storage_bucket: str = "villas-del-lago-2-qr-code"
    document_ai_processor_id: str = "7be5c9a4bea94049"
    document_ai_location: str = "us"

    openai_model: str = "gpt-5-nano"
    chat_model: str = "gpt-5-nano"
    chat_max_history: int = 10
    chat_max_tokens: int = 1000

    max_document_size: int = 10 * 1024 * 1024  # 10MB
    visit_expiration_ms: int = 24 * 60 * 60 * 1000  # 24h
    expire_interval_minutes: int = 10
    visitor_watch_window: int = 1000  # how many recent visitors the listener keeps an eye on

    # auth
    service_api_key: str = ""  # shared secret for the n8n / whatsapp side

    # storage urls — flipping use_signed_urls on requires
    # roles/iam.serviceAccountTokenCreator and a coordinated app release
    use_signed_urls: bool = False
    signed_url_ttl_minutes: int = 15

    # transport limits
    cors_origins: str = ""  # comma-separated; empty = no browser origin allowed
    max_request_bytes: int = 15 * 1024 * 1024  # rejected before the route runs
    max_chat_message_chars: int = 4000
    min_search_term_chars: int = 3

    # how many proxies append to X-Forwarded-For between us and the client.
    # 1 = cloud run direct; 2 = behind an external https load balancer.
    trusted_proxy_hops: int = 1

    # 0 disables the purge; any positive value deletes stored id documents
    # older than that many days
    document_retention_days: int = 0
    purge_interval_hours: int = 24

    # opaque qr object names. requires the gate app to read `qr` from the
    # visitor document rather than rebuilding "{visitorId}.png" itself.
    opaque_qr_filenames: bool = False

    # rate limits (requests / window seconds)
    ip_rate_limit: int = 120
    ip_rate_window: int = 60
    chat_rate_limit: int = 30
    chat_rate_window: int = 60
    extraction_rate_limit: int = 20
    extraction_rate_window: int = 60

    enable_listeners: bool = True
    enable_scheduler: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()

MIME_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
}


class COLLECTIONS:
    VISITORS = "visitors"
    AUTHORIZED_USERS = "authorizedUsers"
    FCM_TOKENS = "fcmTokens"
    SITE_CONFIG = "siteConfig"
    ANNOUNCEMENT_DOC = "announcement"


class ERR:
    MISSING_DOCUMENT = "Document in base64 format is required"
    MISSING_MIME_TYPE = "MIME type is required"
    MISSING_PHONE_NUMBER = "Phone Number was not provided"
    MISSING_UNIT_NUMBER = "Unit number is required"
    PROFILE_NOT_FOUND = "Profile not found"
    OPENAI_NOT_CONFIGURED = "OpenAI API key not configured. Please set OPENAI_API_KEY"
    DOCUMENT_TOO_LARGE = "Document size exceeds maximum allowed size"
    NO_TEXT_EXTRACTED = "No text could be extracted from the image"
    DOCUMENT_AI_AUTH_ERROR = "Authentication failed or insufficient permissions for Document AI"
    VISION_API_AUTH_ERROR = "Authentication failed or insufficient permissions for Vision API"
    INVALID_MESSAGES = "Messages must be a non-empty array of {role, content} objects"
    INVALID_BASE64 = "Document is not valid base64"
    MISSING_CREDENTIALS = "Authentication required"
    INVALID_CREDENTIALS = "Invalid or expired credentials"
    FORBIDDEN = "You do not have access to this resource"
    PROFILE_INACTIVE = "Profile is not active"
    UNIT_NOT_ALLOWED = "You can only register visitors for your own unit"
    SERVICE_AUTH_NOT_CONFIGURED = "Service authentication is not configured"
    RATE_LIMITED = "Too many requests, slow down"
    REQUEST_TOO_LARGE = "Request body is too large"
    MESSAGE_TOO_LONG = "A message exceeds the maximum allowed length"
    INTERNAL_ERROR = "Internal server error"


class CODE:
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    NO_TEXT_ERROR = "NO_TEXT_ERROR"
    CREATE_VISITOR_ERROR = "CREATE_VISITOR_ERROR"
    RETRIEVE_PROFILE_ERROR = "RETRIEVE_PROFILE_ERROR"
    DOCUMENT_AI_ERROR = "DOCUMENT_AI_ERROR"
    VISION_AI_ERROR = "VISION_AI_ERROR"
    OPENAI_ERROR = "OPENAI_ERROR"
    CHAT_ASSISTANT_ERROR = "CHAT_ASSISTANT_ERROR"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
