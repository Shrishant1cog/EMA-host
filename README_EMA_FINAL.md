# EMA Final Integrated Build

This build combines the upgraded temporal-intelligence backend with the interactive, responsive dark frontend and separate admin control center.

## Included
- AI backend using Groq GPT-OSS 120B with GPT-OSS 20B fallback/verifier configuration.
- Strict temporal evidence gating and fail-closed event creation.
- Google Calendar create/update safety checks.
- Server-side session handling, encrypted credential support, scoped worker/inspection controls, admin auth and CSRF protection.
- Dashboard with adaptive mobile/tablet/desktop layout, low-resource performance tier, reduced-motion handling, animated KPI updates, quick-actions palette (`Ctrl/Cmd + K`), connection health badge and entry video.
- Settings retains dark-oriented modes only; legacy `light` values are migrated to dark.
- Dedicated `/admin` control center with user/account counts, account email details, processing activity, errors/warnings, logs, memory/worker/inspection controls and AI configuration visibility through the backend.
- Lightweight 3.6 second packaged EMA intro video (~176 KB).
- Static frontend/backend smoke/self-tests.

## Install
```powershell
python -m pip install -r requirements.txt
```

## Local environment
Copy `.env.example` to `.env` and fill in your own values. Never commit `.env`.

Generate the token-encryption key:
```powershell
python generate_token_encryption_key.py
```

Generate an admin password hash:
```powershell
python generate_admin_password.py
```

## Validate an existing .env
Your earlier `python-dotenv could not parse statement starting at line 20` warning means the current `.env` contains a malformed line. Run:
```powershell
python tools\validate_env.py .env
```
It reports only the bad line numbers/content prefix and does not print secrets.

## Validate before starting
```powershell
python -m py_compile backend\main.py
python phase1_backend_selftest.py
python phase1_backend_test.py
python tools\frontend_smoke_test.py
```

## Start EMA
Run only one server instance on port 8000:
```powershell
python -m uvicorn backend.main:app --port 8000
```

Open:
- User app: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin`

The backend serves the frontend directly. Do not start a second Uvicorn on the same port.

## Notes
The package contains no real database, OAuth token file, `.env`, API key or generated secret. It is intentionally safe to copy into the project as a replacement build.
