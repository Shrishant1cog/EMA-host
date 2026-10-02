# EMA Persistent Login Fix

## Root cause
The Netlify frontend stored the user session identifier in `sessionStorage`. Browser tabs/windows clearing that storage on close caused the frontend to lose its session identifier even though the backend session could still be valid.

## Fix
- Persist the opaque EMA session identifier in `localStorage`.
- Keep backend-side session validation as the authority.
- Use a rolling expiration (configured to 30 days in production) so active users keep their sign-in.
- Clear the persistent client session on explicit full logout.
- Keep OAuth handoff in the URL fragment only long enough to transfer the session ID into persistent storage, then remove it from the address bar.

## Production requirement
Render must keep the SQLite session database on the persistent disk (`/var/data/assistant.db`).
