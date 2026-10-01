# EMA Pro UX Build

This build keeps the existing EMA backend/API contracts and upgrades the user/admin frontend.

## Entry video behavior
- User dashboard: intro video is blocked until the dashboard authentication check succeeds, then it plays once.
- Admin: the intro video is never shown while the administrator login form is visible. It plays only after a successful admin login.

## Admin local-host fix
`frontend/admin/admin.js` uses the exact page origin by default instead of hardcoding `127.0.0.1`. This avoids the localhost-vs-127.0.0.1 cookie split that can cause a successful admin login to be followed by 401 requests.

## Included
- Professional animated user dashboard UX layer (`frontend/ux-pro.css`, `frontend/ux-pro.js`)
- Reworked admin control center (`frontend/admin/index.html`, `admin.css`, `admin.js`)
- Existing Phase-1 backend and AI stack
- Existing tools and validation scripts
- Existing entry video and assets


## Final entry-video and auth fixes

The final build uses a strict auth-gated intro flow:

- User: the entry video is paused/unloaded before authentication and starts only after `/api/user` returns `authenticated=true`.
- Admin: the entry overlay is hidden and `preload=none` on the login screen; the video is loaded and played only after successful admin login/session verification.
- Browser session handling no longer creates a duplicate client-side `assistant_session_id` cookie; the server's HttpOnly cookie remains authoritative while the dashboard uses the session header for explicit transport.
- Local HTTP admin sessions are not marked `Secure`, preventing the login-success/401 loop observed on localhost.
- Settings are dark/read only.

Run the final audit with:

```powershell
python tools\ux_contract_test.py
python tools\frontend_smoke_test.py
python phase1_backend_selftest.py
python phase1_backend_test.py
python -m py_compile backend\main.py
```
