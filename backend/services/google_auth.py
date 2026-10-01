import base64
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import id_token
from googleapiclient.discovery import build

from backend.utils.state_tracker import (
    delete_google_account,
    get_google_account,
    list_all_google_accounts,
    set_account_monitoring_status,
    upsert_google_account,
)
import config

logger = logging.getLogger("GoogleAuth")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TOKEN_PATH = BASE_DIR / "token.json"

_AUTH_LOCK = threading.RLock()
REAUTH_NEEDED_ACCOUNTS: Set[str] = set()

# Thread-isolated client storage: Prevents SSL socket collisions across threads
_THREAD_LOCAL = threading.local()

_REFRESH_LOCKS_GUARD = threading.RLock()
_REFRESH_LOCKS: Dict[str, threading.RLock] = {}

def _get_refresh_lock(email: str) -> threading.RLock:
    key = email.strip().lower()
    with _REFRESH_LOCKS_GUARD:
        return _REFRESH_LOCKS.setdefault(key, threading.RLock())



def _get_thread_service_cache() -> Dict[str, Dict[str, Any]]:
    if not hasattr(_THREAD_LOCAL, "services"):
        _THREAD_LOCAL.services = {}
    return _THREAD_LOCAL.services


def invalidate_service_cache(email: Optional[str] = None):
    """Evicts cached Google API clients when credentials change or disconnect."""
    cache = _get_thread_service_cache()
    with _AUTH_LOCK:
        if email:
            clean_email = email.strip().lower()
            cache.pop(clean_email, None)
        else:
            cache.clear()


def mark_account_needs_reauth(email: str):
    clean_email = email.strip().lower()
    with _AUTH_LOCK:
        REAUTH_NEEDED_ACCOUNTS.add(clean_email)
        invalidate_service_cache(clean_email)


def clear_account_reauth(email: str):
    clean_email = email.strip().lower()
    with _AUTH_LOCK:
        REAUTH_NEEDED_ACCOUNTS.discard(clean_email)


def is_reauth_needed(email: str) -> bool:
    clean_email = email.strip().lower()
    with _AUTH_LOCK:
        return clean_email in REAUTH_NEEDED_ACCOUNTS


SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.appdata",
]


def decode_jwt_payload(jwt_str: str) -> Dict[str, Any]:
    if not jwt_str or not isinstance(jwt_str, str):
        return {}
    try:
        parts = jwt_str.split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            decoded_bytes = base64.urlsafe_b64decode(padded.encode("utf-8"))
            return json.loads(decoded_bytes.decode("utf-8", errors="ignore"))
    except Exception:
        pass
    return {}


def auto_migrate_legacy_token():
    if not TOKEN_PATH.exists():
        return
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_id_token = data.get("id_token")
        if not raw_id_token:
            logger.warning("Legacy token found without an ID token; skipping automatic identity migration.")
            return

        client_id = getattr(config, "GOOGLE_CLIENT_ID", "").strip()
        if not client_id:
            logger.warning("Legacy token migration skipped: GOOGLE_CLIENT_ID is not configured.")
            return

        try:
            payload = id_token.verify_oauth2_token(raw_id_token, Request(), client_id)
        except Exception as verify_err:
            logger.warning(f"Legacy token migration rejected: Google ID token verification failed ({type(verify_err).__name__}).")
            return

        email = str(payload.get("email") or "").strip().lower()
        if not email:
            return
        name = str(payload.get("name") or "User")[:200]
        picture = str(payload.get("picture") or "")[:2000]
        clean_data = dict(data)
        clean_data.pop("client_id", None)
        clean_data.pop("client_secret", None)

        if not get_google_account(email):
            upsert_google_account(
                email=email,
                name=name,
                picture=picture,
                token_data=json.dumps(clean_data),
                is_monitored=1,
            )
    except Exception as e:
        logger.warning(f"Could not auto-migrate legacy token: {type(e).__name__}: {e}")

auto_migrate_legacy_token()


def get_credentials_for_account(email: str) -> Optional[Credentials]:
    if not email:
        return None
    clean_email = email.strip().lower()

    # Database access is serialized only for short reads/writes. Network token refreshes
    # happen under a per-account lock, never under the global authentication lock.
    with _AUTH_LOCK:
        acc = get_google_account(clean_email)
    if not acc or not acc.get("token_data"):
        logger.error(f"No account record or token_data found for: {clean_email}")
        return None

    with _get_refresh_lock(clean_email):
        try:
            from datetime import datetime
            data = json.loads(acc["token_data"])
            token = data.get("token") or data.get("access_token")
            refresh_token = data.get("refresh_token")
            token_uri = data.get("token_uri", "https://oauth2.googleapis.com/token")
            client_id = data.get("client_id") or getattr(config, "GOOGLE_CLIENT_ID", "").strip()
            client_secret = data.get("client_secret") or getattr(config, "GOOGLE_CLIENT_SECRET", "").strip()
            scopes = data.get("scopes") or SCOPES

            # Legacy fallback: borrow refresh token only when the account does not have one.
            # Never persist client_secret/client_id in the per-account token blob.
            if not refresh_token and TOKEN_PATH.exists():
                try:
                    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
                        legacy_data = json.load(f)
                    refresh_token = legacy_data.get("refresh_token")
                    if refresh_token:
                        data["refresh_token"] = refresh_token
                        clean_data = dict(data)
                        clean_data.pop("client_id", None)
                        clean_data.pop("client_secret", None)
                        upsert_google_account(
                            email=clean_email,
                            name=acc.get("name") or clean_email,
                            picture=acc.get("picture") or "",
                            token_data=json.dumps(clean_data),
                            session_id=acc.get("session_id"),
                            is_monitored=acc.get("is_monitored", 1),
                        )
                except Exception as legacy_err:
                    logger.debug(f"Legacy refresh-token fallback skipped for {clean_email}: {legacy_err}")

            creds = Credentials(
                token=token,
                refresh_token=refresh_token,
                token_uri=token_uri,
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes,
            )

            if data.get("expiry"):
                try:
                    exp_str = str(data["expiry"]).replace("Z", "+00:00")
                    parsed_exp = datetime.fromisoformat(exp_str)
                    creds.expiry = parsed_exp.replace(tzinfo=None) if parsed_exp.tzinfo else parsed_exp
                except Exception:
                    pass

            if (creds.expired or not creds.valid) and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError as refresh_err:
                    logger.error(f"Google refresh token expired for {clean_email}: {refresh_err}")
                    mark_account_needs_reauth(clean_email)
                    return None

                clear_account_reauth(clean_email)
                invalidate_service_cache(clean_email)
                updated_dict = json.loads(creds.to_json())
                if not updated_dict.get("refresh_token"):
                    updated_dict["refresh_token"] = refresh_token
                updated_dict.pop("client_id", None)
                updated_dict.pop("client_secret", None)

                upsert_google_account(
                    email=clean_email,
                    name=acc.get("name") or clean_email,
                    picture=acc.get("picture") or "",
                    token_data=json.dumps(updated_dict),
                    session_id=acc.get("session_id"),
                    is_monitored=acc.get("is_monitored", 1),
                )

            return creds if creds.valid else None

        except Exception as e:
            logger.error(f"Error loading credentials for account {clean_email}: {type(e).__name__}: {e}")
            return None

def get_google_services_for_account(email: str) -> Tuple[Any, Any]:
    clean_email = email.strip().lower()
    cache = _get_thread_service_cache()

    if clean_email in cache and not is_reauth_needed(clean_email):
        c = cache[clean_email]
        if "gmail" in c and "calendar" in c:
            return c["gmail"], c["calendar"]

    creds = get_credentials_for_account(clean_email)
    if not creds:
        raise RuntimeError(f"No valid credentials found for account: {clean_email}.")

    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False, static_discovery=False)
    calendar = build("calendar", "v3", credentials=creds, cache_discovery=False, static_discovery=False)

    if clean_email not in cache:
        cache[clean_email] = {}
    cache[clean_email]["gmail"] = gmail
    cache[clean_email]["calendar"] = calendar

    return gmail, calendar


def get_drive_service_for_account(email: str) -> Any:
    clean_email = email.strip().lower()
    cache = _get_thread_service_cache()

    if clean_email in cache and not is_reauth_needed(clean_email):
        c = cache[clean_email]
        if "drive" in c:
            return c["drive"]

    creds = get_credentials_for_account(clean_email)
    if not creds:
        raise RuntimeError(f"No valid credentials found for account: {clean_email}.")

    drive = build("drive", "v3", credentials=creds, cache_discovery=False, static_discovery=False)

    if clean_email not in cache:
        cache[clean_email] = {}
    cache[clean_email]["drive"] = drive

    return drive


def get_account_auth_state(email: str) -> Dict[str, Any]:
    """Non-network credential health check for admin/UI status pages."""
    clean_email = str(email or "").strip().lower()
    if not clean_email:
        return {"exists": False, "needs_reauth": True, "expired": True}
    acc = get_google_account(clean_email)
    if not acc or not acc.get("token_data"):
        return {"exists": False, "needs_reauth": True, "expired": True}
    if is_reauth_needed(clean_email):
        return {"exists": True, "needs_reauth": True, "expired": True}
    try:
        data = json.loads(acc["token_data"])
        expiry = data.get("expiry")
        expired = False
        if expiry:
            from datetime import datetime, timezone
            value = str(expiry).replace("Z", "+00:00")
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            expired = dt <= datetime.now(timezone.utc)
        return {"exists": True, "needs_reauth": False, "expired": expired, "has_refresh_token": bool(data.get("refresh_token"))}
    except Exception:
        return {"exists": True, "needs_reauth": True, "expired": True}


def get_monitored_accounts() -> List[Dict[str, Any]]:
    all_accs = list_all_google_accounts()
    monitored = []
    for a in all_accs:
        if a.get("is_monitored") == 1:
            a["email"] = a["email"].strip().lower()
            monitored.append(a)
    return monitored