# EMA Phase 1 — Enhanced Backend

This package is the Phase 1 backend implementation based on the current uploaded backend files.

## Main improvements

### AI / temporal intelligence
- Primary Groq model: `openai/gpt-oss-120b`.
- Fast fallback / verifier: `openai/gpt-oss-20b`.
- High reasoning by default for primary and verifier.
- Strict JSON Schema mode for the supported GPT-OSS models.
- Prompt-injection-aware source handling.
- Deterministic non-event/spam gates before expensive inference.
- Evidence-hint extraction for dates, times, relative terms and meeting URLs.
- Temporal-evidence gate: a model proposal cannot become an event without source-side temporal evidence.
- Second-pass AI verification for candidate events / low-confidence results.
- Only `CREATE` is automatically executable; update/cancel proposals without an existing event identifier are rejected safely.
- Completion-length failures are treated as provider errors instead of partial valid output.

### Authentication / authorization
- Expiring server-side user sessions.
- HttpOnly session cookie support.
- Admin username/password authentication with PBKDF2-SHA256 hashing.
- Admin sessions are server-side, expiring, HttpOnly and SameSite=Strict.
- Admin write actions require CSRF token.
- Admin login throttling.
- Worker controls are scoped to the requesting user's accounts; they no longer globally pause another user's inboxes.
- Historical inspection has an owner session and another user cannot stop/read someone else's active inspection.
- Calendar target accounts must belong to the same user's linked Google-account session.
- GET logout is non-destructive; account deletion occurs on POST logout.

### OAuth / credential security
- Production HTTPS is required for non-loopback deployments; insecure OAuth transport is local-development-only.
- Google ID tokens are cryptographically verified during legacy migration / identity handling.
- Per-account stored credential JSON no longer persists client ID/client secret unnecessarily.
- Optional/production-enforced Fernet encryption is supported for stored token blobs.
- Use `generate_token_encryption_key.py` to create `TOKEN_ENCRYPTION_KEY`.

### Memory / performance
- Accurate process RSS measurement through psutil when available, with verified Windows/Linux fallback.
- Bounded in-process admin/session caches.
- Session last-seen SQLite writes are throttled to reduce disk I/O.
- Attachment memory budget per email is bounded.
- Google service refresh no longer holds the global auth lock across network calls.
- AI prompts are compacted to controlled input budgets.
- Rotating logs are bounded and no longer flush every record solely for the removed user-facing SSE terminal stream.

### State / calendar correctness
- Persistent bounded processed-email index.
- Failed email IDs are kept out of the processed index so failures can be retried.
- Processing stats have a separate `failed` count.
- Calendar parsing fails closed on invalid timestamps; it never invents tomorrow.
- AI reminders and RRULE recurrence are carried into Google Calendar payloads when valid.
- Calendar creation results are checked for `created`, `duplicate_skipped`, or failure before an email is marked successful.

### Admin diagnostics
- Persistent `ema_errors.jsonl` ledger.
- Service-level WARN/ERROR records are routed into the admin error ledger.
- Errors are categorized for the separate admin Errors section.
- `/api/admin/ai/status` reports AI model routing and configuration without returning API keys.
- `/health` reports memory, AI readiness, encryption readiness and API version metadata.

## Files

```text
backend/
  main.py
  models/event_schemas.py
  services/ai_service.py
  services/calendar_service.py
  services/drive_service.py
  services/drive_state_adapter.py
  services/gmail_service.py
  services/google_auth.py
  utils/dns_resolver.py
  utils/logger.py
  utils/state_tracker.py
config.py
requirements.txt
generate_admin_password.py
generate_token_encryption_key.py
phase1_backend_selftest.py
phase1_backend_test.py
.env.backend.example
```

## Setup

1. Back up your existing backend first.
2. Replace the Phase 1 backend files with the package files.
3. Install/update dependencies:

```powershell
python -m pip install -r requirements.txt
```

4. Generate the credential-encryption key:

```powershell
python generate_token_encryption_key.py
```

Copy its output to `.env` in production.

5. Generate an admin password hash:

```powershell
python generate_admin_password.py
```

6. Production `.env` should include:

```env
BACKEND_URL=https://your-domain.example
SESSION_SECURE_COOKIE=true
TOKEN_ENCRYPTION_REQUIRED=true
TOKEN_ENCRYPTION_KEY=...
EMA_ADMIN_USERNAME=...
EMA_ADMIN_PASSWORD_HASH=...
EMA_ADMIN_SESSION_TTL=28800
EMA_ADMIN_SECURE_COOKIE=true
GROQ_API_KEYS=...
GROQ_MODEL=openai/gpt-oss-120b
GROQ_REASONING_EFFORT=high
AI_VERIFIER_ENABLED=true
AI_VERIFIER_MODEL=openai/gpt-oss-20b
AI_VERIFIER_REASONING_EFFORT=high
AI_EVIDENCE_HINTS_ENABLED=true
```

For local HTTP, `SESSION_SECURE_COOKIE=false` and `EMA_ADMIN_SECURE_COOKIE=false` are acceptable.

## Validation

Run:

```powershell
python phase1_backend_test.py
python phase1_backend_selftest.py
python -m compileall -q backend
```

A live AI test requires the Groq SDK and valid key(s). Phase 2 will connect these backend contracts to the upgraded frontend and admin UI.
