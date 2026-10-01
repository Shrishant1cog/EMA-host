#!/usr/bin/env python3
import getpass, hashlib, secrets
ITERATIONS=600_000
password=getpass.getpass("Admin password: ")
confirm=getpass.getpass("Confirm password: ")
if len(password)<12: raise SystemExit("Use at least 12 characters.")
if password!=confirm: raise SystemExit("Passwords do not match.")
salt=secrets.token_bytes(24)
digest=hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS, dklen=32)
print("EMA_ADMIN_PASSWORD_HASH="+f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}")
