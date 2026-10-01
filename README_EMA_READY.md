# EMA — Website Ready Build

This build keeps the existing backend/application behavior while reducing frontend startup and interaction overhead.

## Entry video
`frontend/assets/ema-entry.mp4` is the project entry video. It is **not downloaded or played before authentication**. After successful user/admin authentication, the video is loaded once, shown with a lightweight poster, and fail-opens if playback is blocked or slow.

## Local run
1. Copy `.env.example` to `.env` and fill in your real credentials.
2. Install `requirements.txt`.
3. Start: `python -m uvicorn backend.main:app --port 8000`.
4. Open `http://127.0.0.1:8000/`.

For local development, `config.py` loads the project `.env` over stale process-level values. For deployment, set `EMA_ENV=production` so deployment environment variables remain authoritative.

## QA
See `QA_REPORT_FINAL.md` and `tools/full_qa.py`. The final automated suite passed; the only unexecuted live suite in the agent environment is the Google/Groq integration test because `google_auth_oauthlib` is not installed in the container.

REVISION 2026-10-01: Spacious UI pass, fixed desktop header wrapping, removed Skip intro controls, reduced glass/ripple/polling overhead, improved responsive spacing.
