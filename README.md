# EMA Secure Admin Login

The admin portal no longer depends on `EMA_ADMIN_EMAILS`.

## 1. Generate your password hash

```powershell
python generate_admin_password.py
```

Enter a password of at least 12 characters.

## 2. Put the username and hash in the server environment

```env
EMA_ADMIN_USERNAME=your_admin_username
EMA_ADMIN_PASSWORD_HASH=pbkdf2_sha256$600000$...
EMA_ADMIN_SESSION_TTL=28800
EMA_ADMIN_SECURE_COOKIE=false
```

Do not commit `.env`. Add `.env` to `.gitignore` if it is not already ignored.

For production HTTPS:

```env
EMA_ADMIN_SECURE_COOKIE=true
```

## 3. Install the patch

Replace:

```text
backend/main.py
frontend/admin/index.html
frontend/admin/admin.js
frontend/admin/admin.css
```

with the files in this package.

## 4. Restart EMA

Stop the currently running Uvicorn process before starting the new one.

## 5. Open

```text
http://127.0.0.1:8000/admin
```

You will get a dedicated admin username/password screen.

## Security

- PBKDF2-SHA256 password hash, not plaintext password storage
- Random 48-byte-equivalent URL-safe server session token
- Session token is only in an HttpOnly cookie
- SameSite=Strict
- Configurable expiration, max 12 hours
- 5 failed logins per 15-minute client window
- Constant-time username/password comparisons
- CSRF token required for admin POST actions
- Logout revokes the server-side session
- No admin credentials or session token in localStorage
- No OAuth token data returned by the admin APIs

Admin dashboard read APIs require the admin session. Admin mutation APIs additionally require the CSRF header.
