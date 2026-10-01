@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo EMA SECURE ADMIN SETUP
echo ============================================
echo.
echo 1. Generate a password hash:
python generate_admin_password.py
echo.
echo 2. Add the generated hash and a username to your EMA .env:
echo EMA_ADMIN_USERNAME=your_admin_username
echo EMA_ADMIN_PASSWORD_HASH=pbkdf2_sha256$600000$...
echo EMA_ADMIN_SESSION_TTL=28800
echo EMA_ADMIN_SECURE_COOKIE=false
echo.
echo Do NOT paste the raw password into source code or commit .env.
echo For HTTPS production set EMA_ADMIN_SECURE_COOKIE=true.
echo.
pause
