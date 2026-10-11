# EMA Production Launch Checklist

Do not mark the deployment ready solely because the Render build succeeds. The `/health` route now returns HTTP 503 while required production configuration is missing.

## 1. Configure a real frontend custom domain

1. In Netlify, add the domain you own under **Domain management** and follow the DNS records Netlify gives you. Do not guess A/CNAME records; use the values shown for your site.
2. Wait until Netlify reports the domain and HTTPS certificate as active.
3. Set Render's `FRONTEND_URL` to the exact public HTTPS origin, for example `https://app.your-owned-domain.example` (replace with the domain you actually own). Do not leave it at a hosting-provider subdomain.
4. Set Render's `REDIRECT_URI` to the backend OAuth callback URL, currently `https://ema-host.onrender.com/auth/callback`. Google redirects to this backend URL. The backend then redirects to `FRONTEND_URL` with a short-lived, single-use exchange ticket; the frontend exchanges it for an HttpOnly session cookie through the Netlify `/api/*` proxy. If the backend hostname changes, update the redirect URI and proxy targets consistently.
5. In Google Cloud Console, add the exact backend callback URL to the OAuth client's **Authorized redirect URIs**. This is a server-side OAuth flow, so the frontend does not normally need to be added as a Google **Authorized JavaScript origin**.
6. Redeploy the frontend after DNS/HTTPS activation and verify its custom domain opens the actual frontend.

Official guides: [Netlify custom domains](https://docs.netlify.com/domains-https/custom-domains/) and [Netlify rewrites/proxies](https://docs.netlify.com/manage/routing/redirects/rewrites-proxies/).

## 2. Configure Render storage and secrets

The repository's `render.yaml` expects one standard web-service instance with a persistent disk mounted at `/var/data`. Confirm these values in the **actual existing service's** Render dashboard; the file only configures the service automatically when that service is managed/synced as a Blueprint.

- Persistent disk mount path: `/var/data`
- `DB_PATH=/var/data/assistant.db`
- `EMA_RENDER_DISK_MOUNT_PATH=/var/data`
- `EMA_ENV=production`
- `EMA_REQUIRE_CUSTOM_DOMAIN=true`
- `SESSION_SECURE_COOKIE=true`
- `TOKEN_ENCRYPTION_REQUIRED=true`
- `FRONTEND_URL`: the verified custom HTTPS frontend origin
- `REDIRECT_URI`: the exact Google OAuth callback URL
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
- `GROQ_API_KEYS`
- `TOKEN_ENCRYPTION_KEY`: generate once and store safely. Do not regenerate it after account tokens have been encrypted, or those tokens will become unreadable.
- `EMA_ADMIN_USERNAME` and `EMA_ADMIN_PASSWORD_HASH`

Render supplies `RENDER_EXTERNAL_URL` automatically. Do not create a manual Render environment variable with that reserved name. The `/health` endpoint checks that the SQLite database exists on the mounted disk and that required production configuration is present.

If the existing Render service is not managed by this repository's Blueprint, apply the start command manually as:
```sh
uvicorn backend.main:app --host 0.0.0.0 --port $PORT --no-access-log
```
The `--no-access-log` option avoids writing OAuth callback query strings (which can contain one-time authorization codes and state) into Uvicorn request logs. This must be changed on the actual service, not just committed in `render.yaml`, if the service does not sync Blueprint configuration.

## 3. Confirm same-origin frontend proxying

The static frontend uses relative paths for API and authentication requests. `netlify.toml` therefore rewrites `/api/*`, `/auth/*`, and `/health` to `https://ema-host.onrender.com`. If the Render hostname changes, update all three rewrites and the callback URL together.

## 4. Run authenticated smoke tests

- Open the frontend through the custom domain; check DevTools Network for successful `/api/user`, `/api/accounts`, and `/health` responses without CORS errors.
- Complete a fresh Google sign-in, connect a Gmail account, and confirm the dashboard loads after the OAuth redirect.
- Send a test email containing an explicit date and an attachment. Confirm that EMA records the email and creates the intended Calendar event.
- Allow a retry of the same message and confirm only one Calendar event exists.
- Restart/redeploy the Render service, then verify the connected account, settings, email history, and Calendar reconciliation still work.
- Check the health response's `checks` object. All required production checks must be true; HTTP 503 is intentional while a launch requirement is missing.
- Review backend logs for application errors. OAuth query-string logging is disabled by the repository start command only when that command is applied to the actual Render service.

## Launch rule

Do not merge or announce production readiness until the custom domain and HTTPS are active, the actual Render persistent disk is attached, production secrets are set, CI passes, and the authenticated end-to-end checks above pass. DNS ownership and external dashboard settings cannot be completed by a repository change alone.
