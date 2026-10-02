# EMA — Render backend + Netlify frontend deployment

## Architecture
- Netlify hosts only `frontend/`.
- Netlify rewrites `/api/*`, `/auth/*`, and `/health` to the Render FastAPI service. The browser remains on the Netlify origin.
- Render hosts the FastAPI backend and a persistent SQLite file at `/var/data/assistant.db`.
- User email/action/settings state continues to persist in each user’s private Google Drive appDataFolder through the existing Drive state adapter.

## Important database note
The current code does **not** use Google Drive as a live SQLite database. It uses SQLite for server-side account/session/token metadata and Google Drive for the user’s EMA state. Do not put a live SQLite file in a synced Google Drive folder.

Render’s normal filesystem is ephemeral, so the SQLite database must be placed on a Render persistent disk or replaced with a managed database. Persistent disks require a paid Render web service.

## Render
1. Push the repository to GitHub.
2. Create a Render Web Service from the repository.
3. Build command: `pip install -r requirements.txt`.
4. Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.
5. Health check: `/health`.
6. Attach a persistent disk mounted at `/var/data`.
7. Add all production environment variables from `.env.production.example` in Render — never commit `.env`.
8. Set `BACKEND_URL` and `RENDER_EXTERNAL_URL` to the Render URL.
9. Set `FRONTEND_URL` to the Netlify URL and `REDIRECT_URI` to `https://ema-host.onrender.com/auth/callback` so Google returns directly to the backend callback, which then hands the authenticated session to Netlify.

## Netlify
1. Create a Netlify site from the same Git repository.
2. Publish directory: `frontend`. No build command is required for the static site.
3. Verify `frontend/_redirects` points to `https://ema-host.onrender.com` for `/api/*`, `/auth/*`, and `/health`.
4. Redeploy.

## Google Cloud OAuth
Add this exact production redirect URI to the Google OAuth Web Application client:
`https://ema-host.onrender.com/auth/callback`

The browser starts OAuth through Netlify, Google returns directly to the Render backend callback, Render completes OAuth, then redirects the browser to `https://emasys.netlify.app/index.html#ema_session=...`. The fragment is consumed client-side, removed from the address bar, and the dashboard sends the server-side session identifier as `X-Session-ID` on proxied API requests. This avoids cross-origin cookie loss while keeping the website on Netlify.

For a custom production domain, use the custom domain consistently for `FRONTEND_URL` and `REDIRECT_URI`, and add that exact origin/redirect URI in Google Cloud.

## First production checks
- Open `https://YOUR-NETLIFY-SITE/health` and confirm a successful JSON response.
- Open the login page and complete Google sign-in.
- Confirm the browser stays on the Netlify domain after the OAuth callback.
- Connect Gmail/Calendar and verify the Accounts page.
- Verify the Dashboard can read Drive state.
- Verify an incoming test email produces the expected classification and, when appropriate, a Calendar event.
- Test admin sign-in separately at `/admin`.

## Never put these in Git
`GOOGLE_CLIENT_SECRET`, `GROQ_API_KEYS`, `TOKEN_ENCRYPTION_KEY`, `EMA_ADMIN_PASSWORD_HASH`, OAuth token files, and `.env` files.


## Render wake / continuous operation
The repository includes `.github/workflows/render-warmup.yml`, which requests `/health` every 10 minutes. GitHub documents a five-minute minimum for scheduled workflows and notes that scheduled runs can be delayed. This can keep a Render Free service warm while the workflow is active, but it is not a 24/7 uptime guarantee. Render Free services can still restart; an always-on paid service is the reliable option for a continuous background monitor.
