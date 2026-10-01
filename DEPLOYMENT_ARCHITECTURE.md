# EMA production architecture

Browser -> Netlify (frontend + same-origin proxy) -> Render (FastAPI backend) -> Google APIs / Groq
                                                   |
                                                   +--> /var/data/assistant_v2.db (Render persistent disk)

User-specific EMA state (email action history, settings and Drive-backed state) remains in the authenticated user's private Google Drive appDataFolder.
