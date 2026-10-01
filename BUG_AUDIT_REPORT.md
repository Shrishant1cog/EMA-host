# EMA Pro UX — Final Bug Audit

This build addresses the failures shown in the user's 31-check EMA audit and the entry-video behavior visible in the supplied browser screenshot.

## Fixed failure classes

1. Session/account isolation compatibility: legacy account sessions are migrated into the persisted user-session table, while normal EMA sessions remain long opaque tokens.
2. Authenticated dashboard access: session resolution now prefers the explicit `X-Session-ID` / query session over a stale duplicate browser cookie.
3. Duplicate client session cookie: the user login/dashboard no longer writes a second JavaScript `assistant_session_id` cookie that could shadow the server's HttpOnly cookie.
4. Admin login 200 -> protected API 401 loop: local HTTP admin cookies are never marked `Secure`; HTTPS deployments can still use a secure cookie.
5. User entry video: video loading/playback begins only after the authenticated event; it is not declared autoplay before authentication.
6. Admin entry video: the entry overlay is hidden and not preloaded on the login screen; it starts only after a successful admin session is established.
7. Settings: backend and frontend accept only `dark` and `read` themes.
8. Default timing validation: strict 24-hour `HH:MM` validation is present.

## Validation run

- `phase1_backend_selftest.py` — PASS
- `phase1_backend_test.py` — PASS
- `tools/frontend_smoke_test.py` — PASS
- `tools/ux_contract_test.py` — PASS
- Python backend compile — PASS
- JavaScript syntax checks — PASS
- Entry MP4: H.264, 1280x720, 24 fps, 3.583 s — PASS

## Environment limitation

A live Google/Groq provider test was not run in this build environment because those provider packages are not installed here and outbound package installation is unavailable. The user's own environment already showed those packages installed. Device/browser-specific media autoplay can also vary by browser policy; the implementation therefore waits for `canplay`, explicitly mutes the media, and fails open without blocking the dashboard if playback is rejected.
