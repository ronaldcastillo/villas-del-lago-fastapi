# Villas del Lago FastAPI project

Backend for **Villas del Lago**, my residential community (yes, I live here) in the Dominican Republic. It powers visitor control: residents pre-register who's coming, security verifies IDs at the gate, and everyone gets pushed the right notification at the right moment. As part of the HOA, I wanted to contribute to the security of my community so I decided to create an application for managing the visitor's log and improve security.

## The goal

Guests arriving at a gated community used to mean a phone call to the guard for every visitor. The flow this service implements instead:

1. A resident registers an expected visitor — via WhatsApp, or by chatting with the in-app assistant in Spanish.
2. The visitor gets a QR code; security gets a push notification that someone is expected.
3. At the gate, security scans the QR or photographs the visitor's *cédula*; OCR pulls out the ID number, name and date of birth automatically.
4. The visit is marked completed → the resident's household gets a "your visitor has arrived" push.
5. Anything nobody showed up for expires on its own after 24 hours.

Everything user-facing is in Dominican Spanish, and the assistant is prompted to understand local slang and misspellings (`klk`, `ta to`, `bisitante`) without correcting the user.

## Services used

| Service | What it does here |
|---|---|
| **Twilio (WhatsApp) via n8n** | The WhatsApp channel residents use to register visitors. Twilio's webhook lands in an **n8n** workflow, which calls `POST /visitors` and `GET /profiles` here with an `X-Service-Key` header. This service never sees Twilio's request, so **Twilio signature validation belongs in the n8n workflow**, not here. Two traces remain in this code: `sanitize_phone()` strips Twilio's `whatsapp:` prefix (and the `+1` country code) before matching a phone against `authorizedUsers`, and visitors created over that path are stamped `source: "whatsapp"`. No Twilio SDK or credentials are needed to run this. |
| **OpenAI** | Two jobs. (1) The chat assistant (`POST /chat`) — tool-calling over `gpt-5-nano` with a role-specific system prompt and tool set. (2) Parsing raw OCR text of Dominican ID documents into `{documentId, name, dob}` JSON, in the `vision-ai` extraction path. |
| **Google Document AI** | Default ID-extraction engine. A trained processor returns typed entities (`ID`, `Name`, `DOB`) directly — no LLM in the loop. |
| **Google Cloud Vision** | Fallback extraction engine: plain OCR text detection, then handed to OpenAI to structure. |
| **Firebase / Firestore** | System of record — `visitors`, `authorizedUsers`, `fcmTokens`, `siteConfig`. Also the event source: the realtime listeners are Firestore snapshot watches. |
| **Firebase Cloud Messaging** | Push notifications to security and to residents. Dead tokens are pruned from `fcmTokens` on send failure. |
| **Google Cloud Storage** | Holds generated visitor QR PNGs and the original ID document uploads. URLs are returned in the Firebase Storage `?alt=media` shape the app already reads. |
| **Google Cloud Run** | Where it's deployed. Pinned to a single always-on instance — see [Deploy](#deploy-cloud-run). |

## Architecture

Three things run inside one process:

**HTTP API** (`app/routers/`)

| Route | Auth | Does |
|---|---|---|
| `POST /visitors` → 201 | user **or** service | creates a visitor + QR in storage (`createVisitorLogEntry`) |
| `GET /profiles?phoneNumber=…` | service only | authorizedUsers lookup by phone (`retrieveProfileByPhoneNumber`) |
| `POST /documents/extractions` → 201 | user, `security`/`admin` | `{document, mimeType, engine}` → `{documentId, name, dob, documentUrl}`; `engine` is `document-ai` (default, was `processDocumentWithAI`) or `vision-ai` (was `processDocumentWithVisionAndAI`) |
| `POST /chat` | user | OpenAI tool-calling assistant → `{reply, action, card}` (`chatAssistant`) |
| `GET /healthz` | none | liveness |

### Authentication

Two credential types, both in `app/auth.py`:

- **User** — `Authorization: Bearer <firebase-id-token>`. Verified with `firebase_admin.auth.verify_id_token`, then resolved to a `Principal` by loading the caller's `authorizedUsers` profile. Inactive or missing profiles are rejected with 403.
- **Service** — `X-Service-Key: <shared secret>`, compared with `secrets.compare_digest` against `SERVICE_API_KEY`. This is n8n's credential.

**Role and identity are never read from the request body.** `POST /chat` builds its `userContext` entirely from the verified token — a client claiming `{"userContext": {"role": "admin"}}` gets resident tools, because the role comes from Firestore. Likewise `POST /visitors` overrides `userId`/`reportedBy`/`reportedByNumber` with the token's values on the user path, and residents may only register visitors for their own unit (`security`/`admin` may register for any).

Also applied globally: a per-IP rate limit and a 15 MB request-body cap (`app/limits.py`), a CORS allowlist that is **empty by default** (the app and n8n are not browsers — set `CORS_ORIGINS` only if you add a web client), and error responses that return a correlation ref instead of the exception text.

The rate-limit key comes from `X-Forwarded-For` read **from the right**, because the left-hand entries are whatever the caller sent. `TRUSTED_PROXY_HOPS` says how many proxies append to that header between you and the client: `1` for Cloud Run direct (the default), `2` behind an external HTTPS load balancer. Set it wrong and you either share one bucket across all clients (too high) or let clients forge their own key (too low).

**Realtime listeners** (`app/listeners/`, started by the `app/main.py` lifespan)

- `visitors.py`: watches the most recent visitors; a new doc → push to security, `completed` false→true (and not expired) → push to that unit's residents. Replaces the Node `onDocumentCreated` / `onDocumentUpdated` triggers.
- `announcement.py`: watches `siteConfig/announcement` and broadcasts to every registered token, once per `updatedAt` (idempotent via `lastNotifiedAt`).
- `manager.py`: a 60-second watchdog that restarts a watch if the Firestore SDK closes it on a non-retryable error.

**Scheduled job** (`app/jobs/`)

- `expire_visits.py`: every 10 minutes, marks pending visits past `expiresAt` as expired.

### How the chat assistant works

`POST /chat` takes `{messages, userContext}` and returns `{reply, action, card}`. The tool set and system prompt are built from `userContext.role` — `user`, `admin` or `security` see different capabilities.

The split that matters: **reads run on the backend, writes run on the client.** Read-only tools (`get_my_visitors`, `lookup_resident`, `get_recent_visits`, `search_address_book`, `show_security_phone`) are executed here against Firestore and come back as a formatted `reply` plus an optional `card` for the app to render. Write tools (`create_visitor`, `create_quick_service`, `update_phone_number`) are *not* executed — the service returns a Spanish confirmation prompt plus an `action` describing the intent, and the app performs the write only after the user confirms. The model never silently mutates data.

Only the first tool call in a response is honored.

## Run locally

```bash
uv sync
cp .env.example .env   # add OPENAI_API_KEY and SERVICE_API_KEY
gcloud auth application-default login   # firestore / fcm / storage / docai / vision
uv run uvicorn app.main:app --reload
```

Generate the service key with `openssl rand -hex 32`. Without `SERVICE_API_KEY` set, the service-authenticated routes return 503 rather than falling open.

Set `ENABLE_LISTENERS=false ENABLE_SCHEDULER=false` if you just want the HTTP side without creds.

## Tests

```bash
uv run pytest -q
```

No GCP creds needed — Firestore/OpenAI/FCM are stubbed (`tests/fake_firestore.py`).

## Deploy (Cloud Run)

```bash
gcloud run deploy villas-del-lago-api --source . --region us-central1 \
  --min-instances=1 --max-instances=1 --no-cpu-throttling --memory=512Mi \
  --set-secrets OPENAI_API_KEY=OPENAI_API_KEY:latest,SERVICE_API_KEY=SERVICE_API_KEY:latest \
  --allow-unauthenticated
```

`--allow-unauthenticated` is still correct — the mobile app reaches the service directly and authentication is enforced in-process by `app/auth.py`, not by Cloud Run IAM.

The listeners and the cron live inside the process, so:

- keep `min=max=1` and CPU always on — more instances = duplicate pushes, no instances = nothing listens
- a deploy briefly runs two revisions; you may get a duplicate visitor push during that window
- visitor events while the service is down are not replayed (announcements are — they're idempotent on `lastNotifiedAt`)
- the service account needs Firestore, FCM, Storage, Document AI and Vision access
- rate-limit state is in-process, which is sound *because* the deploy is pinned to one instance; if that ever changes, move it to Redis or Cloud Armor

Firestore composite indexes needed (same as the Node version): `visitors(completed, expiresAt)`, `visitors(userId, completed, createdAt desc)`, `visitors(unitNumber, completed, completedAt desc)`, `authorizedUsers(unitNumber, isActive)`, `authorizedUsers(role, isActive)`.

## Config

Everything is an env var, see `app/config.py`. Defaults match the old `constants.js` (project id, bucket, Document AI processor, `gpt-5-nano`). `OPENAI_API_KEY` and `SERVICE_API_KEY` are the two you must set; GCP credentials come from ADC or `GOOGLE_APPLICATION_CREDENTIALS`.

Three settings are **off by default because turning them on requires a coordinated client or IAM change**. Each is safe to flip once the matching prerequisite is done:

| Setting | What it does | Prerequisite |
|---|---|---|
| `USE_SIGNED_URLS` | serves stored documents as short-lived V4 signed URLs instead of the public `?alt=media` form | `roles/iam.serviceAccountTokenCreator` on the runtime service account (ADC on Cloud Run has no private key to sign with), plus an app release. Then revoke public read on the bucket. |
| `OPAQUE_QR_FILENAMES` | stores QR images under a random name instead of `{visitorId}.png` | the gate app must read `qr` from the visitor document rather than rebuilding the path. `POST /visitors` now writes `qr` onto the document, so that is already available. |
| `DOCUMENT_RETENTION_DAYS` | enables the ID-document purge job | decide the retention window; it permanently deletes objects. |

**Note on existing objects:** documents uploaded before this change are still named `{cedula}_{random}.ext`. New uploads are opaque, but the old ones are not renamed — if the bucket is publicly readable, those object names still carry the national ID. Rename or delete them as a one-off alongside revoking public read.

Responses use the same envelope as the Node version: `{success, data, timestamp}` / `{success: false, error: {message, statusCode, code, timestamp}}`.

## WhatsApp bot
There is a WhatsApp-powered bot using Twilio for allowing the residents to report their visitors directly from WhatsApp using either text or audio (voice notes). The flow is powered by n8n and connects an AI agent for all of the interaction with the residents.
<img width="1691" height="662" alt="image" src="https://github.com/user-attachments/assets/f5d96a0a-a2e1-4343-b0fd-bcecc5554e6e" />

