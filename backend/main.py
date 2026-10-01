import os
import re
import sys
import gc
import json
import logging
import time
import uuid
import html
import ctypes
import threading
import subprocess
import webbrowser
import requests
import hmac
import hashlib
import secrets
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from google_auth_oauthlib.flow import Flow
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError
from google.oauth2 import id_token as google_id_token
from google.auth.transport.requests import Request as GoogleRequest

os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"]:
    os.environ.pop(proxy_var, None)

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
sys.path.append(str(BASE_DIR))

try:
    from backend.utils.dns_resolver import install_resilient_dns
    install_resilient_dns()
except Exception:
    pass

import config

# OAuth insecure transport is permitted only for local loopback development.
if not getattr(config, "BACKEND_URL", "").lower().startswith(("http://127.0.0.1", "http://localhost")):
    os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
else:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from backend.utils.logger import setup_logger
from backend.utils.state_tracker import (
    init_db,
    upsert_google_account,
    get_google_account,
    list_all_google_accounts,
    set_account_monitoring_status,
    delete_google_account,
    migrate_token_data_to_encrypted,
    create_user_session,
    is_user_session_valid,
    touch_user_session,
    revoke_user_session,
    ensure_legacy_session,
)
from backend.services.drive_state_adapter import (
    is_email_processed_drive,
    record_email_action_drive,
    get_drive_stats,
    list_drive_emails,
    get_drive_setting,
    set_drive_setting,
    is_reauth_needed,
    clear_account_reauth,
)
from backend.services.google_auth import (
    SCOPES,
    get_credentials_for_account,
    get_google_services_for_account,
    get_drive_service_for_account,
    get_monitored_accounts,
    decode_jwt_payload,
    get_account_auth_state,
)
from backend.services.gmail_service import (
    fetch_email_details,
    get_unprocessed_message_ids,
)
from backend.services.drive_service import upload_evidence_to_drive
from backend.services.ai_service import extract_events_from_email
from backend.services.calendar_service import create_or_update_event

LOG_FILE = getattr(config, "LOG_FILE", BASE_DIR / "calendar_assistant.log")
init_db()

logger = setup_logger("WebController")


def _valid_hhmm(value: str) -> bool:
    """Validate strict 24-hour HH:MM input."""
    if not isinstance(value, str):
        return False
    return re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value.strip()) is not None


try:
    migrated_tokens = migrate_token_data_to_encrypted()
    if migrated_tokens:
        logger.info(f"Encrypted {migrated_tokens} existing Google credential record(s) at rest.")
except Exception as exc:
    logger.warning(f"Credential encryption migration skipped: {exc}")


_AUTH_LOCK = threading.RLock()
_AUTH_FLOWS: Dict[str, Dict[str, Any]] = {}
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ==============================================================================
# ADAPTIVE MEMORY GOVERNOR
# ==============================================================================
class AdaptiveMemoryGovernor:
    _libc = None
    _has_trim = False

    @classmethod
    def initialize(cls):
        if sys.platform.startswith("linux"):
            try:
                cls._libc = ctypes.CDLL("libc.so.6")
                cls._has_trim = hasattr(cls._libc, "malloc_trim")
            except Exception:
                cls._has_trim = False

    @classmethod
    def get_memory_usage_mb(cls) -> float:
        """Return current resident/working-set memory accurately across Windows/Linux."""
        try:
            try:
                import psutil
                return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)
            except Exception:
                pass

            if sys.platform.startswith("linux"):
                with open("/proc/self/statm", "r", encoding="utf-8") as f:
                    pages = int(f.read().split()[1])
                return round((pages * os.sysconf("SC_PAGE_SIZE")) / (1024 * 1024), 2)

            if sys.platform == "win32":
                import ctypes.wintypes
                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", ctypes.wintypes.DWORD),
                        ("PageFaultCount", ctypes.wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t),
                    ]
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return round(counters.WorkingSetSize / (1024 * 1024), 2)
        except Exception:
            pass
        return -1.0

    @classmethod
    def trim(cls, force: bool = False):
        try:
            gc.collect(1)
            if force:
                gc.collect()
            if cls._has_trim and cls._libc:
                cls._libc.malloc_trim(0)
        except Exception:
            pass


AdaptiveMemoryGovernor.initialize()


def read_log_tail_fixed_buffer(file_path: Path, max_lines: int = 25, buffer_size: int = 16384) -> List[str]:
    if not file_path.exists():
        return []
    try:
        with open(file_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_len = f.tell()
            seek_pos = max(0, file_len - buffer_size)
            f.seek(seek_pos)
            chunk = f.read().decode("utf-8", errors="replace")
            lines = chunk.splitlines()
            if seek_pos > 0 and len(lines) > 1:
                lines = lines[1:]
            return [line.strip() for line in lines[-max_lines:] if line.strip()]
    except Exception:
        return []


def resolve_user_display_name(scoped_accs: List[Dict[str, Any]], primary_email: str) -> Tuple[str, bool]:
    custom_name = get_drive_setting(primary_email, "custom_display_name", "").strip()
    if custom_name:
        return custom_name, True

    candidates: List[Tuple[int, str]] = []
    for acc in scoped_accs:
        name = (acc.get("name") or "").strip()
        if name and "@" not in name and name.lower() not in ("user", "admin", "google user", "unknown"):
            word_count = len(name.split())
            is_title = name.istitle()
            length = len(name)
            score = (word_count * 15) + (5 if is_title else 0) + min(length, 25)
            candidates.append((score, name))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1], False

    clean_local = primary_email.split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ")
    return clean_local.title(), False


# ==============================================================================
# KEEP-ALIVE WORKER
# ==============================================================================
KEEP_ALIVE_STOP_EVENT = threading.Event()
KEEP_ALIVE_THREAD: Optional[threading.Thread] = None


def keep_alive_worker():
    server_url = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("BACKEND_URL") or "").strip().rstrip("/")
    if not server_url:
        return

    health_url = f"{server_url}/health"
    while not KEEP_ALIVE_STOP_EVENT.is_set():
        if KEEP_ALIVE_STOP_EVENT.wait(timeout=300):
            break
        try:
            requests.get(health_url, timeout=10)
        except Exception:
            pass
        finally:
            AdaptiveMemoryGovernor.trim()


def emit_user_log(level: str, message: str):
    lvl = level.upper()
    if lvl in ("ERROR", "FAIL"):
        logger.error(message)
    elif lvl in ("WARN", "WARNING"):
        logger.warning(message)
    elif lvl == "FILTER":
        logger.warning(message)
    else:
        logger.info(message)


def set_auth_flow(sid: str, **kwargs):
    with _AUTH_LOCK:
        now = time.time()
        expired = [k for k, v in _AUTH_FLOWS.items() if now - v.get("timestamp", 0) > 900]
        for k in expired:
            _AUTH_FLOWS.pop(k, None)

        if sid not in _AUTH_FLOWS:
            _AUTH_FLOWS[sid] = {
                "timestamp": now,
                "status": "pending",
                "error": None,
                "email": None,
                "code_verifier": "",
                "redirect_uri": "",
            }
        _AUTH_FLOWS[sid].update(kwargs)
        _AUTH_FLOWS[sid]["timestamp"] = now


def get_auth_flow(sid: str) -> Optional[Dict[str, Any]]:
    with _AUTH_LOCK:
        return _AUTH_FLOWS.get(sid)


def open_in_chrome_or_default(url: str) -> bool:
    if sys.platform == "win32":
        browser_candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.expandvars(r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
        for exe_path in browser_candidates:
            if os.path.isfile(exe_path):
                try:
                    subprocess.Popen(
                        [exe_path, url],
                        creationflags=CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True
                except OSError:
                    pass

        try:
            os.startfile(url)
            return True
        except Exception:
            pass

    try:
        webbrowser.open(url, new=2)
        return True
    except Exception as e:
        logger.warning(f"Browser launch failed: {e}")
        return False


def focus_desktop_window():
    if sys.platform == "win32":
        try:
            ps_script = (
                '$wshell = New-Object -ComObject WScript.Shell; '
                '$wshell.AppActivate("EMA"); '
                '$wshell.AppActivate("Smart Universal Email Assistant"); '
                '$wshell.AppActivate("EmailAutomater")'
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    global KEEP_ALIVE_THREAD
    emit_user_log("INFO", f"EMA service active. Current RAM: {AdaptiveMemoryGovernor.get_memory_usage_mb()} MB")
    
    KEEP_ALIVE_STOP_EVENT.clear()
    # Optional self-health ping. Disabled by default in production because
    # Render manages service health independently.
    external_url = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("BACKEND_URL") or "").strip()
    keepalive_enabled = os.getenv("EMA_ENABLE_KEEPALIVE", "false").strip().lower() in {"1", "true", "yes", "on"}
    if external_url and keepalive_enabled:
        KEEP_ALIVE_THREAD = threading.Thread(target=keep_alive_worker, daemon=True, name="EMA-KeepAlive")
        KEEP_ALIVE_THREAD.start()
    else:
        KEEP_ALIVE_THREAD = None

    yield

    KEEP_ALIVE_STOP_EVENT.set()
    stop_worker_internal()
    if INSPECT_STATE["is_running"]:
        INSPECT_STATE["stop_event"].set()
    AdaptiveMemoryGovernor.trim(force=True)
    emit_user_log("INFO", "EMA service stopped cleanly.")


app = FastAPI(title="EMA - Smart Universal Email Assistant API", lifespan=lifespan)

cors_origins = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
]
frontend_url_env = os.getenv("FRONTEND_URL", "").strip()
if frontend_url_env:
    for raw_origin in frontend_url_env.split(","):
        cleaned_origin = raw_origin.strip().rstrip("/")
        if cleaned_origin and cleaned_origin not in cors_origins:
            cors_origins.append(cleaned_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "X-Session-ID", "X-Requested-With", "X-Request-ID", "X-EMA-Admin-CSRF"],
)


# ---------------------------------------------------------------------------
# Lightweight request diagnostics. No request history is retained in memory.
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_diagnostics(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "").strip()[:64] or secrets.token_hex(8)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        _append_error_ledger(
            "ERROR",
            f"{request.method} {request.url.path} -> unhandled exception: {str(exc)}",
            request_id,
        )
        logger.error(f"[{request_id}] Unhandled {request.method} {request.url.path}: {exc}")
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")

    if request.url.path.startswith("/api/") and response.status_code >= 500:
        _append_error_ledger(
            "ERROR",
            f"{request.method} {request.url.path} -> HTTP {response.status_code} ({duration_ms} ms)",
            request_id,
        )
        logger.error(
            f"[{request_id}] {request.method} {request.url.path} -> HTTP {response.status_code} ({duration_ms} ms)"
        )

    return response


@app.get("/health")
def health_check():
    ram_mb = AdaptiveMemoryGovernor.get_memory_usage_mb()
    return {
        "status": "ok",
        "timestamp": int(time.time()),
        "memory_mb": ram_mb,
        "is_lean": 0 <= ram_mb < 500.0,
        "memory_supported": ram_mb >= 0,
        "error_ledger": ERROR_LEDGER_FILE.exists(),
        "api_version": "2026-09",
        "memory_backend": "psutil-or-windows-psapi-or-linux-proc",
        "credential_encryption": "enabled" if (getattr(config, "TOKEN_ENCRYPTION_KEY", "") or not getattr(config, "TOKEN_ENCRYPTION_REQUIRED", False)) else "missing",
        "session_cookie_secure": bool(getattr(config, "SESSION_SECURE_COOKIE", False)),
        "ai_configured": bool(getattr(config, "GROQ_API_KEYS", [])),
        "ai_primary_model": getattr(config, "GROQ_MODEL", ""),
        "ai_verifier_enabled": bool(getattr(config, "AI_VERIFIER_ENABLED", True)),
    }


def _session_cookie_secure() -> bool:
    raw = os.getenv("SESSION_SECURE_COOKIE", "").strip().lower()
    if raw:
        return raw in ("1", "true", "yes", "on")
    backend_url = (getattr(config, "BACKEND_URL", "") or os.getenv("BACKEND_URL", "")).strip().lower()
    return backend_url.startswith("https://")


def get_request_session_id(request: Request) -> Optional[str]:
    # Prefer an explicit header/query session over the browser cookie. This avoids
    # stale/duplicate client cookies shadowing the current OAuth session.
    sid = (
        request.headers.get("x-session-id")
        or request.query_params.get("session_id")
        or request.cookies.get("assistant_session_id")
        or getattr(request.state, "session_id", None)
    )
    if sid in (None, "", "undefined", "null"):
        return None
    sid = sid.strip()
    if len(sid) > 256 or any(ord(ch) < 32 for ch in sid):
        return None
    if is_user_session_valid(sid):
        touch_user_session(sid)
    return sid


def get_accounts_for_session(sid: Optional[str]) -> List[Dict[str, Any]]:
    if not sid:
        return []
    try:
        if not is_user_session_valid(sid):
            if not ensure_legacy_session(sid):
                return []
        accounts = [a for a in list_all_google_accounts() if a.get("session_id") == sid]
        if accounts:
            touch_user_session(sid)
        return accounts
    except Exception as e:
        logger.error(f"Error resolving accounts for session: {e}")
        return []


def verify_account_ownership(email: str, sid: Optional[str]) -> bool:
    if not email or not sid:
        return False
    clean_email = email.strip().lower()
    user_accs = get_accounts_for_session(sid)
    return any(a.get("email", "").strip().lower() == clean_email for a in user_accs)


def get_primary_email_for_session(sid: Optional[str]) -> Optional[str]:
    scoped = get_accounts_for_session(sid)
    return scoped[0]["email"] if scoped else None


def get_dynamic_redirect_uri(request: Request) -> str:
    configured = getattr(config, "REDIRECT_URI", "").strip()
    if configured:
        return configured
    return "http://127.0.0.1:8000/auth/callback"


# ==============================================================================
# PER-ACCOUNT CALENDAR ROUTING PIPELINE
# ==============================================================================
def process_single_email(
    gmail_service, calendar_service, drive_email: str, mid: str, account_email: str
) -> bool:
    clean_acc_email = account_email.strip().lower()
    subject = "No Subject"
    sender = "Unknown"
    email_data = None
    try:
        email_data = fetch_email_details(gmail_service, mid)
        subject = email_data.get("subject", "No Subject")
        sender = email_data.get("sender", "Unknown")

        emit_user_log("INFO", f"Analyzing email from {sender} — '{subject}' [{clean_acc_email}]")

        spam_threshold_pct = int(get_drive_setting(clean_acc_email, "spam_threshold", "50"))
        spam_threshold_ratio = spam_threshold_pct / 100.0
        design_enabled = get_drive_setting(clean_acc_email, "design_events_enabled", "false").lower() == "true"
        user_prompt = get_drive_setting(clean_acc_email, "design_events_prompt", "") if design_enabled else ""
        default_time = get_drive_setting(clean_acc_email, "default_timing", "09:00")

        user_tz = ZoneInfo(getattr(config, "USER_TIMEZONE", "Asia/Kolkata"))
        current_dt = datetime.now(user_tz)
        analysis = extract_events_from_email(
            subject=subject,
            sender=sender,
            date_received=email_data.get("date_received", ""),
            email_body=email_data.get("email_body", ""),
            attachment_text=email_data.get("combined_attachment_text", ""),
            custom_prompt=user_prompt,
            default_time=default_time,
            spam_threshold=spam_threshold_ratio,
            current_datetime=current_dt.isoformat(),
            reference_day=current_dt.strftime("%Y-%m-%d"),
            user_email=clean_acc_email,
            require_document_evidence=getattr(config, "REQUIRE_DOCUMENT_EVIDENCE", False),
        )

        action_taken = analysis.action_description
        status = "PROCESSED"
        category = analysis.category

        spam_protection_enabled = get_drive_setting(clean_acc_email, "spam_protection_enabled", "true").lower() == "true"
        is_spam = (
            getattr(analysis, "is_spam_or_scam", False)
            or getattr(analysis, "spam_score", 0.0) >= spam_threshold_ratio
            or analysis.category == "Spam / Scam"
        )

        if spam_protection_enabled and is_spam:
            score = getattr(analysis, "spam_score", 0.8)
            pct = int(score * 100) if score > 0 else 80
            category = "Spam / Scam"
            status = "FILTERED"
            action_taken = f"Ignored - Detected as Spam/Scam ({pct}% confidence)"
            emit_user_log("FILTER", f"Filtered spam email '{subject}' for {clean_acc_email} ({pct}% confidence).")
        # STRICT EVENT GATEKEEPER: ONLY schedule if confirmed meeting/deadline category
        elif (
            analysis.has_calendar_event
            and analysis.events
            and analysis.category in ("Meeting & Event", "Task & Deadline")
        ):
            has_files = (
                bool(email_data.get("has_attachments"))
                or len(email_data.get("attachments", [])) > 0
                or bool(email_data.get("combined_attachment_text"))
            )
            only_files_required = get_drive_setting(clean_acc_email, "only_remind_with_files", "false").lower() == "true"

            if only_files_required and not has_files:
                action_taken = "Calendar reminder skipped (No file attached per settings)"
                status = "FILTERED"
                emit_user_log("INFO", f"Skipped calendar for '{subject}': No attachment attached.")
            else:
                uploaded_attachments = []
                raw_attachments = email_data.get("attachments", [])

                if raw_attachments and (has_files or getattr(analysis, "is_attachment_evidence", False)):
                    try:
                        drive_service = get_drive_service_for_account(clean_acc_email)
                        for att in raw_attachments:
                            if att.get("data"):
                                drive_meta = upload_evidence_to_drive(
                                    drive_service,
                                    filename=att["filename"],
                                    file_bytes=att["data"],
                                    mime_type=att.get("mime_type", "application/octet-stream"),
                                )
                                if drive_meta:
                                    uploaded_attachments.append(drive_meta)
                                att["data"] = None
                    except Exception as drive_err:
                        emit_user_log("WARN", f"Could not save attachments to Drive for {clean_acc_email}: {drive_err}")

                # Target Calendar Resolution: only the same user's linked accounts are permitted.
                target_setting = get_drive_setting(clean_acc_email, "target_calendar_account", "auto").strip().lower()
                target_cal_email = clean_acc_email if (not target_setting or target_setting == "auto") else target_setting
                source_account = get_google_account(clean_acc_email)
                target_account = get_google_account(target_cal_email) if target_cal_email != clean_acc_email else source_account
                if (target_cal_email != clean_acc_email and
                        (not target_account or not source_account or target_account.get("session_id") != source_account.get("session_id"))):
                    emit_user_log("ERROR", f"Rejected invalid calendar target {target_cal_email} for {clean_acc_email}.")
                    status = "FAILED"
                    action_taken = "Calendar scheduling failed: target calendar is not owned by the same user."
                    raise RuntimeError(action_taken)

                target_cal_service = calendar_service
                if target_cal_email != clean_acc_email:
                    try:
                        _, target_cal_service = get_google_services_for_account(target_cal_email)
                    except Exception as target_err:
                        emit_user_log("ERROR", f"Target calendar unavailable for {target_cal_email}: {target_err}")
                        status = "FAILED"
                        action_taken = f"Calendar scheduling failed: target calendar unavailable"
                        raise RuntimeError(action_taken)

                scheduled_names = []
                successful_count = 0
                failed_events = []
                for ev in analysis.events:
                    result = create_or_update_event(
                        calendar_service=target_cal_service,
                        event_data=ev,
                        original_sender=sender,
                        attachments=uploaded_attachments,
                        calendar_id=target_cal_email,
                        target_account_email=target_cal_email,
                    )
                    if result.status in ("created", "duplicate_skipped"):
                        successful_count += 1
                        scheduled_names.append(getattr(ev, "title", "Event"))
                    else:
                        failed_events.append(getattr(ev, "title", "Event"))

                evidence_note = f" with {len(uploaded_attachments)} file(s) attached" if uploaded_attachments else ""
                if successful_count == len(analysis.events):
                    action_taken = f"Scheduled {successful_count} event(s) in {target_cal_email} calendar{evidence_note}"
                    status = "ACTIONED"
                    emit_user_log("SUCCESS", f"[{target_cal_email}] Scheduled: {', '.join(scheduled_names)}{evidence_note}")
                elif successful_count > 0:
                    action_taken = f"Scheduled {successful_count}/{len(analysis.events)} event(s); {len(failed_events)} failed"
                    status = "FAILED"
                    emit_user_log("ERROR", f"[{target_cal_email}] Partial scheduling failure: {', '.join(failed_events)}")
                else:
                    action_taken = "Calendar scheduling failed for all candidate events"
                    status = "FAILED"
                    emit_user_log("ERROR", f"[{target_cal_email}] All calendar event creations failed")
        elif analysis.category == "General / Marketing":
            status = "FILTERED"
            emit_user_log("FILTER", f"Organized newsletter/marketing email: '{subject}'")
        else:
            status = "PROCESSED"
            emit_user_log("INFO", f"Processed email '{subject}' for {clean_acc_email} — No calendar action needed.")

        record_email_action_drive(
            account_email=clean_acc_email,
            action_data={
                "email_id": str(mid),
                "account_email": clean_acc_email,
                "sender": sender[:100],
                "subject": subject[:150],
                "category": category,
                "priority": analysis.priority,
                "summary": analysis.summary[:250],
                "action_taken": action_taken[:250],
                "status": status,
                "confidence_score": float(getattr(analysis, "confidence_score", 0.0)),
                "spam_score": float(getattr(analysis, "spam_score", 0.0)),
                "primary_source_type": getattr(analysis, "primary_source_type", "unknown"),
                "reasoning": str(getattr(analysis, "reasoning", ""))[:500],
                "processed_at": datetime.now().isoformat(),
            },
        )

        # Keep failed messages unread so the worker can retry them later.
        if status != "FAILED":
            try:
                gmail_service.users().messages().modify(
                    userId="me", id=str(mid), body={"removeLabelIds": ["UNREAD"]}
                ).execute()
            except HttpError as mark_err:
                emit_user_log("WARN", f"Processed email but could not remove UNREAD label for '{subject}': {mark_err}")
            return True

        return False

    except Exception as e:
        emit_user_log("ERROR", f"Failed to process email '{subject}' for {clean_acc_email}: {str(e)}")
        record_email_action_drive(
            account_email=clean_acc_email,
            action_data={
                "email_id": str(mid),
                "account_email": clean_acc_email,
                "sender": sender[:100],
                "subject": subject[:150],
                "category": "Failed",
                "priority": "LOW",
                "summary": f"Error: {str(e)}"[:250],
                "action_taken": "Failed processing",
                "status": "FAILED",
                "processed_at": datetime.now().isoformat(),
            },
        )
        return False
    finally:
        if email_data:
            email_data.clear()
            del email_data


# ==============================================================================
# BACKGROUND WORKER
# ==============================================================================
WORKER_STATE = {
    "is_running": False,
    "last_sync": None,
    "current_status": "Idle",
    "stop_event": threading.Event(),
}
WORKER_THREAD: Optional[threading.Thread] = None
_WORKER_SCOPE_LOCK = threading.RLock()
_WORKER_PAUSED_SESSIONS: set[str] = set()


def _session_worker_enabled(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    with _WORKER_SCOPE_LOCK:
        return session_id not in _WORKER_PAUSED_SESSIONS


def _set_session_worker_enabled(session_id: str, enabled: bool) -> bool:
    sid = str(session_id or '').strip()
    if not sid:
        return False
    with _WORKER_SCOPE_LOCK:
        if enabled:
            _WORKER_PAUSED_SESSIONS.discard(sid)
        else:
            _WORKER_PAUSED_SESSIONS.add(sid)
    return True


def _has_any_active_worker_scope() -> bool:
    accounts = get_monitored_accounts()
    return any(_session_worker_enabled(a.get('session_id')) for a in accounts)


def background_worker_loop():
    emit_user_log("INFO", "Multi-account background monitor active.")
    WORKER_STATE["is_running"] = True
    WORKER_STATE["current_status"] = "Live"

    IDLE_SLEEP = 60
    ACTIVE_SLEEP = 15

    while not WORKER_STATE["stop_event"].is_set():
        try:
            accounts = get_monitored_accounts()
            active_accounts = [
                a for a in accounts
                if not a.get("session_id") or _session_worker_enabled(a.get("session_id"))
            ]
            if not active_accounts:
                WORKER_STATE["current_status"] = "Idle"
                if WORKER_STATE["stop_event"].wait(timeout=15):
                    break
                continue

            WORKER_STATE["current_status"] = "Live"
            WORKER_STATE["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            total_new_emails = 0

            for acc in active_accounts:
                if WORKER_STATE["stop_event"].is_set():
                    break

                acc_email = acc["email"].strip().lower()
                try:
                    last_epoch_str = get_drive_setting(acc_email, "last_sync_epoch", "0")
                    current_epoch = int(time.time())

                    if not last_epoch_str or last_epoch_str == "0":
                        epoch_cutoff = current_epoch - (24 * 3600)
                        set_drive_setting(acc_email, "last_sync_epoch", str(current_epoch))
                    else:
                        try:
                            epoch_cutoff = int(last_epoch_str)
                        except ValueError:
                            epoch_cutoff = current_epoch - 3600

                    gmail_service, calendar_service = get_google_services_for_account(acc_email)
                    query = f"is:unread label:INBOX -label:spam -label:trash after:{epoch_cutoff}"
                    msg_ids = get_unprocessed_message_ids(gmail_service, query=query, max_results=5)

                    if msg_ids:
                        total_new_emails += len(msg_ids)
                        emit_user_log("INFO", f"Found {len(msg_ids)} new unread email(s) in {acc_email}.")

                    for mid in msg_ids:
                        if WORKER_STATE["stop_event"].is_set():
                            break
                        if is_email_processed_drive(acc_email, mid):
                            continue

                        process_single_email(gmail_service, calendar_service, acc_email, mid, acc_email)

                    set_drive_setting(acc_email, "last_sync_epoch", str(current_epoch))

                except (RefreshError, HttpError) as auth_e:
                    emit_user_log("WARN", f"Authentication error for {acc_email}: {auth_e}")
                except Exception as acc_err:
                    emit_user_log("ERROR", f"Error checking inbox for {acc_email}: {acc_err}")

            AdaptiveMemoryGovernor.trim()

            sleep_duration = ACTIVE_SLEEP if total_new_emails > 0 else IDLE_SLEEP
            if WORKER_STATE["stop_event"].wait(timeout=sleep_duration):
                break

        except Exception as loop_err:
            emit_user_log("ERROR", f"Worker loop iteration error: {loop_err}")
            if WORKER_STATE["stop_event"].wait(timeout=10):
                break

    WORKER_STATE["is_running"] = False
    WORKER_STATE["current_status"] = "Idle"
    emit_user_log("INFO", "Background monitor paused.")


def start_worker_internal():
    global WORKER_THREAD
    if WORKER_STATE["is_running"]:
        return
    WORKER_STATE["stop_event"].clear()
    WORKER_THREAD = threading.Thread(target=background_worker_loop, daemon=True)
    WORKER_THREAD.start()


def stop_worker_internal():
    WORKER_STATE["stop_event"].set()


# ==============================================================================
# INSPECTOR ENGINE
# ==============================================================================
INSPECT_STATE = {
    "is_running": False,
    "current_count": 0,
    "target_count": 10,
    "status_message": "Ready to inspect",
    "stop_event": threading.Event(),
    "owner_session": None,
}
INSPECT_THREAD: Optional[threading.Thread] = None


def inspect_previous_loop(max_target: int, scoped_accounts: List[Dict[str, Any]]):
    clamped_target = min(max(10, max_target), 40)
    INSPECT_STATE["is_running"] = True
    INSPECT_STATE["target_count"] = clamped_target
    INSPECT_STATE["current_count"] = 0
    INSPECT_STATE["status_message"] = f"Scanning {clamped_target} unprocessed emails..."
    emit_user_log("INFO", f"Starting historical email scan (target: {clamped_target} emails).")

    try:
        if not scoped_accounts:
            INSPECT_STATE["status_message"] = "No accounts enabled for monitoring."
            return

        found_unprocessed = 0

        for acc in scoped_accounts:
            if INSPECT_STATE["stop_event"].is_set() or found_unprocessed >= clamped_target:
                break

            acc_email = acc["email"].strip().lower()
            try:
                gmail_service, calendar_service = get_google_services_for_account(acc_email)
                results = gmail_service.users().messages().list(
                    userId="me",
                    q="label:INBOX -label:spam -label:trash",
                    maxResults=25,
                ).execute()

                for m in results.get("messages", []):
                    if INSPECT_STATE["stop_event"].is_set() or found_unprocessed >= clamped_target:
                        break

                    mid = m["id"]
                    if is_email_processed_drive(acc_email, mid):
                        continue

                    INSPECT_STATE["status_message"] = f"Analyzing [{acc_email}] email {found_unprocessed + 1} of {clamped_target}..."
                    process_single_email(gmail_service, calendar_service, acc_email, mid, acc_email)
                    found_unprocessed += 1
                    INSPECT_STATE["current_count"] = found_unprocessed
            except Exception as e:
                emit_user_log("ERROR", f"Historical scan error on {acc_email}: {str(e)}")

        if INSPECT_STATE["stop_event"].is_set():
            INSPECT_STATE["status_message"] = f"Stopped. Processed {found_unprocessed} email(s)."
        elif found_unprocessed == 0:
            INSPECT_STATE["status_message"] = "All inbox emails have already been processed."
            emit_user_log("INFO", "Historical scan completed: No unprocessed emails found.")
        else:
            INSPECT_STATE["status_message"] = f"Complete! Processed {found_unprocessed} email(s)."
            emit_user_log("SUCCESS", f"Historical scan finished. Processed {found_unprocessed} email(s).")
    finally:
        INSPECT_STATE["is_running"] = False
        INSPECT_STATE["owner_session"] = None
        AdaptiveMemoryGovernor.trim(force=True)



# ==============================================================================
# ADMIN CONTROL CENTER (SECURE SERVER-SIDE AUTHENTICATION)
# ==============================================================================
SERVER_STARTED_AT = time.time()

# Bounded presence cache. It stores only session IDs and timestamps; no email body.
_SESSION_LAST_SEEN: Dict[str, float] = {}
_SESSION_LAST_SEEN_LOCK = threading.RLock()
_SESSION_LAST_SEEN_MAX = 2048

# Bounded in-process admin sessions. Raw tokens never live here.
_ADMIN_SESSIONS: Dict[str, Dict[str, Any]] = {}
_ADMIN_SESSIONS_LOCK = threading.RLock()
_ADMIN_MAX_SESSIONS = 128

# Brute-force protection.
_ADMIN_LOGIN_ATTEMPTS: Dict[str, List[float]] = {}
_ADMIN_LOGIN_LOCK = threading.RLock()
try:
    _ADMIN_MAX_LOGIN_ATTEMPTS = min(max(int(os.getenv("EMA_ADMIN_MAX_LOGIN_ATTEMPTS", "5")), 3), 10)
except Exception:
    _ADMIN_MAX_LOGIN_ATTEMPTS = 5
try:
    _ADMIN_LOGIN_WINDOW_SECONDS = min(max(int(os.getenv("EMA_ADMIN_LOGIN_WINDOW_SECONDS", "900")), 60), 3600)
except Exception:
    _ADMIN_LOGIN_WINDOW_SECONDS = 900
_ADMIN_MAX_SESSION_AGE = 12 * 3600

# Disk-backed error history; RAM usage remains bounded.
ERROR_LEDGER_FILE = BASE_DIR / "ema_errors.jsonl"
_ERROR_LEDGER_LOCK = threading.RLock()
_ERROR_LEDGER_MAX_BYTES = 10 * 1024 * 1024


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _get_admin_username() -> str:
    return os.getenv("EMA_ADMIN_USERNAME", "").strip()


def _get_admin_password_hash() -> str:
    return os.getenv("EMA_ADMIN_PASSWORD_HASH", "").strip()


def _get_admin_session_ttl() -> int:
    try:
        return min(max(int(os.getenv("EMA_ADMIN_SESSION_TTL", "28800")), 900), _ADMIN_MAX_SESSION_AGE)
    except Exception:
        return 28800


def _admin_cookie_secure(request: Request) -> bool:
    # Never mark a localhost HTTP cookie as Secure: browsers will store it but
    # refuse to send it back, which manifests as a login-success/401 loop.
    proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    scheme = proto or request.url.scheme.lower()
    if scheme != "https":
        return False
    return _env_bool("EMA_ADMIN_SECURE_COOKIE", True)


def _parse_pbkdf2_password_hash(encoded: str) -> Optional[Tuple[int, bytes, bytes]]:
    try:
        scheme, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        iterations_i = int(iterations)
        if scheme != "pbkdf2_sha256" or not 100_000 <= iterations_i <= 2_000_000:
            return None
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if len(salt) < 16 or len(expected) != 32:
            return None
        return iterations_i, salt, expected
    except Exception:
        return None


def _verify_admin_password(password: str, encoded: str) -> bool:
    parsed = _parse_pbkdf2_password_hash(encoded)
    if not parsed:
        return False
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
    return hmac.compare_digest(actual, expected)


def _hash_admin_session(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cleanup_admin_sessions(now: Optional[float] = None):
    now = now if now is not None else time.time()
    with _ADMIN_SESSIONS_LOCK:
        expired = [k for k, v in _ADMIN_SESSIONS.items() if float(v.get("expires_at", 0)) <= now]
        for k in expired:
            _ADMIN_SESSIONS.pop(k, None)
        if len(_ADMIN_SESSIONS) > _ADMIN_MAX_SESSIONS:
            oldest = sorted(_ADMIN_SESSIONS.items(), key=lambda x: float(x[1].get("created_at", 0)))
            for k, _ in oldest[:len(_ADMIN_SESSIONS) - _ADMIN_MAX_SESSIONS]:
                _ADMIN_SESSIONS.pop(k, None)


def _admin_client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64] or "unknown"
    return (request.client.host if request.client else "unknown")[:64]


def _admin_login_allowed(request: Request) -> bool:
    key = _admin_client_key(request)
    now = time.time()
    with _ADMIN_LOGIN_LOCK:
        recent = [t for t in _ADMIN_LOGIN_ATTEMPTS.get(key, []) if now - t < _ADMIN_LOGIN_WINDOW_SECONDS]
        _ADMIN_LOGIN_ATTEMPTS[key] = recent
        return len(recent) < _ADMIN_MAX_LOGIN_ATTEMPTS


def _record_admin_login_failure(request: Request):
    key = _admin_client_key(request)
    now = time.time()
    with _ADMIN_LOGIN_LOCK:
        recent = [t for t in _ADMIN_LOGIN_ATTEMPTS.get(key, []) if now - t < _ADMIN_LOGIN_WINDOW_SECONDS]
        recent.append(now)
        _ADMIN_LOGIN_ATTEMPTS[key] = recent


def _admin_login_retry_after(request: Request) -> int:
    key = _admin_client_key(request)
    now = time.time()
    with _ADMIN_LOGIN_LOCK:
        recent = [t for t in _ADMIN_LOGIN_ATTEMPTS.get(key, []) if now - t < _ADMIN_LOGIN_WINDOW_SECONDS]
        _ADMIN_LOGIN_ATTEMPTS[key] = recent
        if not recent:
            return 0
        return max(1, int(_ADMIN_LOGIN_WINDOW_SECONDS - (now - min(recent))))


def _clear_admin_login_failures(request: Request):
    with _ADMIN_LOGIN_LOCK:
        _ADMIN_LOGIN_ATTEMPTS.pop(_admin_client_key(request), None)


def _create_admin_session() -> Tuple[str, str, float]:
    raw = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    now = time.time()
    expires = now + _get_admin_session_ttl()
    with _ADMIN_SESSIONS_LOCK:
        _cleanup_admin_sessions(now)
        _ADMIN_SESSIONS[_hash_admin_session(raw)] = {
            "created_at": now,
            "expires_at": expires,
            "csrf_token": csrf,
        }
    return raw, csrf, expires


def _get_admin_session(request: Request) -> Optional[Dict[str, Any]]:
    raw = request.cookies.get("ema_admin_session", "").strip()
    if not raw:
        return None
    key = _hash_admin_session(raw)
    now = time.time()
    with _ADMIN_SESSIONS_LOCK:
        item = _ADMIN_SESSIONS.get(key)
        if not item:
            return None
        if float(item.get("expires_at", 0)) <= now:
            _ADMIN_SESSIONS.pop(key, None)
            return None
        return item


def _revoke_admin_session(request: Request):
    raw = request.cookies.get("ema_admin_session", "").strip()
    if raw:
        with _ADMIN_SESSIONS_LOCK:
            _ADMIN_SESSIONS.pop(_hash_admin_session(raw), None)


def require_admin(request: Request) -> Dict[str, Any]:
    if not _get_admin_username() or not _get_admin_password_hash():
        raise HTTPException(
            status_code=503,
            detail="Admin credentials are not configured. Set EMA_ADMIN_USERNAME and EMA_ADMIN_PASSWORD_HASH."
        )
    session = _get_admin_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Admin session required.")
    return session


def require_admin_write(request: Request) -> Dict[str, Any]:
    session = require_admin(request)
    supplied = request.headers.get("x-ema-admin-csrf", "")
    expected = str(session.get("csrf_token", ""))
    if not supplied or not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Admin CSRF validation failed.")
    return session


class AdminLoginPayload(BaseModel):
    username: str
    password: str


class AdminEmailPayload(BaseModel):
    email: str


class AdminMonitoringPayload(BaseModel):
    email: str
    is_monitored: bool


class AdminInspectPayload(BaseModel):
    count: int


def _append_error_ledger(level: str, message: str, request_id: str = ""):
    try:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": str(level).upper(),
            "message": str(message)[:6000],
        }
        if request_id:
            record["request_id"] = request_id[:64]

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _ERROR_LEDGER_LOCK:
            ERROR_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)

            # Rotate by renaming the disk file; never load the whole file into RAM.
            try:
                if ERROR_LEDGER_FILE.exists() and ERROR_LEDGER_FILE.stat().st_size > _ERROR_LEDGER_MAX_BYTES:
                    rotated = ERROR_LEDGER_FILE.with_suffix(".jsonl.1")
                    if rotated.exists():
                        rotated.unlink()
                    ERROR_LEDGER_FILE.replace(rotated)
            except OSError:
                pass

            with open(ERROR_LEDGER_FILE, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        # Error reporting can never break EMA itself.
        pass



def _read_error_ledger(limit: int = 120) -> List[Dict[str, Any]]:
    limit = min(max(int(limit), 1), 500)
    lines = read_log_tail_fixed_buffer(ERROR_LEDGER_FILE, max_lines=limit, buffer_size=262144)
    out: List[Dict[str, Any]] = []
    for line in lines:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out[-limit:]


class _BackendErrorLedgerHandler(logging.Handler):
    """Captures service-level WARN/ERROR records in the bounded admin error ledger."""
    _allowed = {
        "AIService", "CalendarService", "GmailService", "DriveService", "DriveStateAdapter",
        "GoogleAuth", "StateTracker", "OCRExtractor", "TextExtractor", "WebController"
    }

    def emit(self, record: logging.LogRecord):
        try:
            if record.levelno < logging.WARNING or record.name not in self._allowed:
                return
            message = record.getMessage()
            if record.exc_info and record.exc_info[1]:
                message = f"{message} | {type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
            _append_error_ledger(record.levelname, message)
        except Exception:
            pass


_ERROR_HANDLER_INSTALLED = False

def _install_error_ledger_handler():
    global _ERROR_HANDLER_INSTALLED
    if _ERROR_HANDLER_INSTALLED:
        return
    handler = _BackendErrorLedgerHandler()
    root = logging.getLogger()
    if handler not in root.handlers:
        root.addHandler(handler)
    # Service loggers set propagate=False, so attach the same handler directly.
    for logger_name in _BackendErrorLedgerHandler._allowed:
        service_logger = logging.getLogger(logger_name)
        if not any(isinstance(h, _BackendErrorLedgerHandler) for h in service_logger.handlers):
            service_logger.addHandler(handler)
    root.setLevel(min(root.level or logging.WARNING, logging.WARNING))
    _ERROR_HANDLER_INSTALLED = True


_install_error_ledger_handler()


def _touch_session(sid: Optional[str]):
    if not sid:
        return
    try:
        with _SESSION_LAST_SEEN_LOCK:
            now = time.time()
            _SESSION_LAST_SEEN[sid] = now
            cutoff = now - 900
            stale = [k for k, v in _SESSION_LAST_SEEN.items() if v < cutoff]
            for key in stale:
                _SESSION_LAST_SEEN.pop(key, None)
            if len(_SESSION_LAST_SEEN) > _SESSION_LAST_SEEN_MAX:
                oldest = sorted(_SESSION_LAST_SEEN.items(), key=lambda x: x[1])
                for key, _ in oldest[:len(_SESSION_LAST_SEEN) - _SESSION_LAST_SEEN_MAX]:
                    _SESSION_LAST_SEEN.pop(key, None)
    except Exception:
        pass


@app.post("/api/admin/auth/login")
def admin_auth_login(payload: AdminLoginPayload, request: Request):
    configured_username = _get_admin_username()
    configured_hash = _get_admin_password_hash()

    if not configured_username or not configured_hash:
        raise HTTPException(status_code=503, detail="Admin credentials are not configured on the server.")
    if not _admin_login_allowed(request):
        response = JSONResponse(
            {"detail": "Too many admin login attempts. Try again later.", "retry_after_seconds": _admin_login_retry_after(request)},
            status_code=429,
        )
        response.headers["Retry-After"] = str(_admin_login_retry_after(request))
        return response

    username_ok = hmac.compare_digest(payload.username.strip(), configured_username)
    password_ok = _verify_admin_password(payload.password, configured_hash)

    if not (username_ok and password_ok):
        _record_admin_login_failure(request)
        logger.warning("[ADMIN_AUTH] Failed admin login attempt")
        _append_error_ledger("WARN", "Failed admin login attempt")
        raise HTTPException(status_code=401, detail="Invalid admin username or password.")

    _clear_admin_login_failures(request)
    raw, csrf, expires = _create_admin_session()

    response = JSONResponse({
        "authenticated": True,
        "role": "admin",
        "username": configured_username,
        "csrf_token": csrf,
        "expires_at": datetime.fromtimestamp(expires).isoformat(),
    })
    response.set_cookie(
        "ema_admin_session",
        raw,
        httponly=True,
        secure=_admin_cookie_secure(request),
        samesite="strict",
        path="/",
        max_age=max(int(expires - time.time()), 900),
    )
    logger.info("[ADMIN_AUTH] Admin login successful")
    return response


@app.get("/api/admin/auth/me")
def admin_auth_me(request: Request):
    session = require_admin(request)
    return {
        "authenticated": True,
        "role": "admin",
        "username": _get_admin_username(),
        "csrf_token": session["csrf_token"],
        "expires_at": datetime.fromtimestamp(float(session["expires_at"])).isoformat(),
    }


@app.post("/api/admin/auth/logout")
def admin_auth_logout(request: Request):
    _revoke_admin_session(request)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(
        "ema_admin_session",
        path="/",
        secure=_admin_cookie_secure(request),
        samesite="strict",
    )
    return response


def _admin_parse_log_line(line: str) -> Dict[str, str]:
    m = re.match(r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s*(.*)$", line.strip())
    if not m:
        return {"time": "", "level": "INFO", "message": line.strip()}
    return {"time": m.group(1), "level": m.group(2).upper(), "message": m.group(3)}


def _admin_error_category(message: str) -> Tuple[str, str]:
    text = (message or "").lower()
    if any(x in text for x in ("11001", "getaddrinfo failed", "name_resolution", "dns", "connection refused", "timed out", "timeout")):
        return "Network / DNS", "Connectivity, DNS lookup, refused connections or timeouts."
    if any(x in text for x in ("invalid_grant", "token has been expired", "insufficientpermissions", "oauth", "authorization", "refresherror", "http 401", "http 403")):
        return "Google OAuth / API", "Google authentication, permissions or API failures."
    if any(x in text for x in ("groq", "rate_limit_exceeded", "invalid_api_key", "model", "llama")):
        return "AI / Groq", "AI provider, quota, model or API-key failures."
    if any(x in text for x in ("jsondecodeerror", "json", "pydantic", "validation", "parse", "extract")):
        return "Parsing / Validation", "Malformed or unvalidated data returned by a parser or model."
    if any(x in text for x in ("calendar", "event", "attendees", "conference", "meet", "zoom", "teams")):
        return "Calendar / Event", "Calendar creation, update, deletion or meeting metadata issues."
    if any(x in text for x in ("ocr", "attachment", "pdf", "docx", "xlsx", "drive", "upload")):
        return "Attachment / Drive / OCR", "Document extraction, OCR or Drive evidence problems."
    if any(x in text for x in ("worker", "background monitor", "historical scan", "inspection")):
        return "Worker / Processing", "Background monitor or historical inspection issues."
    return "Other", "Unclassified application error or warning."


def _admin_log_tail(limit: int = 120) -> List[Dict[str, str]]:
    try:
        raw = read_log_tail_fixed_buffer(Path(LOG_FILE), max_lines=max(200, min(limit * 4, 1200)), buffer_size=65536)
        return [_admin_parse_log_line(line) for line in raw][-limit:]
    except Exception:
        return []


@app.get("/admin", include_in_schema=False)
def admin_page():
    from fastapi.responses import FileResponse
    p = FRONTEND_DIR / "admin" / "index.html"
    if p.exists():
        return FileResponse(p, headers={"Cache-Control": "no-store, max-age=0"})
    return RedirectResponse(url="/admin/index.html", status_code=307)


@app.get("/api/admin/me")
def admin_me(request: Request):
    require_admin(request)
    return {"authenticated": True, "username": _get_admin_username(), "role": "admin"}

@app.get("/api/admin/ai/status")
def admin_ai_status(request: Request):
    require_admin(request)
    return {
        "primary_model": getattr(config, "GROQ_MODEL", ""),
        "fallback_models": list(getattr(config, "GROQ_MODEL_CANDIDATES", []) or []),
        "verifier": {
            "enabled": bool(getattr(config, "AI_VERIFIER_ENABLED", True)),
            "model": getattr(config, "AI_VERIFIER_MODEL", ""),
            "reasoning_effort": getattr(config, "AI_VERIFIER_REASONING_EFFORT", "high"),
        },
        "reasoning_effort": getattr(config, "GROQ_REASONING_EFFORT", "high"),
        "structured_output": True,
        "evidence_hints": bool(getattr(config, "AI_EVIDENCE_HINTS_ENABLED", True)),
        "document_evidence_required": bool(getattr(config, "REQUIRE_DOCUMENT_EVIDENCE", False)),
        "configured_api_keys": len(getattr(config, "GROQ_API_KEYS", []) or []),
        "max_body_chars": int(getattr(config, "AI_MAX_BODY_CHARS", 18000)),
        "max_attachment_chars": int(getattr(config, "AI_MAX_ATTACHMENT_CHARS", 12000)),
        "max_completion_tokens": int(getattr(config, "AI_MAX_COMPLETION_TOKENS", 5000)),
    }


@app.get("/api/admin/overview")
def admin_overview(request: Request):
    require_admin(request)
    accounts = list_all_google_accounts()
    reauth = 0
    monitoring = 0
    processed = 0
    actioned = 0
    filtered = 0
    failed = 0
    for acc in accounts:
        email = acc.get("email", "")
        if acc.get("is_monitored"):
            monitoring += 1
        try:
            health = get_account_auth_state(email)
            if health.get("needs_reauth"):
                reauth += 1
        except Exception:
            pass
        try:
            st = get_drive_stats(email)
            processed += int(st.get("processed", 0))
            actioned += int(st.get("actioned", 0))
            filtered += int(st.get("filtered", 0))
            failed += int(st.get("failed", 0))
        except Exception:
            pass
    parsed = _read_error_ledger(300)
    error_count = sum(1 for x in parsed if x.get("level") == "ERROR")
    warning_count = sum(1 for x in parsed if x.get("level") in ("WARN", "WARNING"))
    ram = AdaptiveMemoryGovernor.get_memory_usage_mb()
    now = time.time()
    with _SESSION_LAST_SEEN_LOCK:
        active_sids = {sid for sid, last in _SESSION_LAST_SEEN.items() if now - last <= 300}
    active_users = len({(a.get("email") or "").strip().lower() for a in accounts if a.get("session_id") in active_sids and a.get("email")})
    return {
        "users": len({(a.get("email") or "").strip().lower() for a in accounts if a.get("email")}),
        "active_users": active_users,
        "accounts": len(accounts),
        "monitoring": monitoring,
        "reauth_required": reauth,
        "processing": {"processed": processed, "actioned": actioned, "filtered": filtered, "failed": failed},
        "errors": {"error_count": error_count, "warning_count": warning_count},
        "health": {
            "memory_mb": ram,
            "is_lean": ram < 500.0,
            "uptime": f"{int(time.time() - SERVER_STARTED_AT) // 3600:02d}:{(int(time.time() - SERVER_STARTED_AT) % 3600) // 60:02d}:{int(time.time() - SERVER_STARTED_AT) % 60:02d}",
            "server_time": datetime.now().isoformat(),
        },
        "worker": {"is_running": WORKER_STATE["is_running"], "status": WORKER_STATE["current_status"], "last_sync": WORKER_STATE["last_sync"]},
        "inspect": {"is_running": INSPECT_STATE["is_running"], "status_message": INSPECT_STATE["status_message"], "current_count": INSPECT_STATE["current_count"], "target_count": INSPECT_STATE["target_count"]},
    }


@app.get("/api/admin/users")
def admin_users(request: Request, limit: int = 500, offset: int = 0):
    require_admin(request)
    all_accounts = list_all_google_accounts()
    clean_limit = min(max(limit, 1), 500)
    start = max(offset, 0)
    out = []
    for acc in all_accounts[start:start + clean_limit]:
        email = (acc.get("email") or "").strip().lower()
        try:
            needs = is_reauth_needed(email) or get_credentials_for_account(email) is None
        except Exception:
            needs = True
        try:
            stats = get_drive_stats(email)
        except Exception:
            stats = {"total": 0, "actioned": 0, "processed": 0, "filtered": 0}
        out.append({
            "email": email,
            "name": acc.get("name") or email,
            "picture": acc.get("picture") or "",
            "is_monitored": bool(acc.get("is_monitored", 1)),
            "created_at": acc.get("created_at"),
            "needs_reauth": needs,
            "stats": stats,
        })
    return {"users": out, "total": len(all_accounts), "offset": start, "limit": clean_limit}


@app.get("/api/admin/users/{email}")
def admin_user_detail(email: str, request: Request):
    require_admin(request)
    clean_email = email.strip().lower()
    acc = get_google_account(clean_email)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found.")
    try:
        needs = is_reauth_needed(clean_email) or get_credentials_for_account(clean_email) is None
    except Exception:
        needs = True
    try:
        stats = get_drive_stats(clean_email)
    except Exception:
        stats = {"total": 0, "actioned": 0, "processed": 0, "filtered": 0}
    recent = []
    try:
        recent = list_drive_emails(clean_email, limit=30, offset=0)
    except Exception:
        recent = []
    settings = {}
    for key, default in (("spam_threshold", "50"),("default_timing","09:00"),("spam_protection_enabled","true"),("only_remind_with_files","false"),("theme_mode","dark"),("target_calendar_account","auto")):
        try:
            settings[key] = get_drive_setting(clean_email, key, default)
        except Exception:
            settings[key] = default
    safe = {k: v for k, v in acc.items() if k != "token_data"}
    safe["needs_reauth"] = needs
    return {"account": safe, "stats": stats, "settings": settings, "recent_actions": recent}


class AdminEmailPayload(BaseModel):
    email: str


class AdminMonitoringPayload(BaseModel):
    email: str
    is_monitored: bool


class AdminInspectPayload(BaseModel):
    count: int


@app.post("/api/admin/users/toggle-monitoring")
def admin_toggle_monitoring(payload: AdminMonitoringPayload, request: Request):
    require_admin_write(request)
    admin_email = _get_admin_username()
    clean_email = payload.email.strip().lower()
    if not get_google_account(clean_email):
        raise HTTPException(status_code=404, detail="Account not found.")
    set_account_monitoring_status(clean_email, payload.is_monitored)
    logger.warning(f"[ADMIN:{admin_email}] Monitoring {'enabled' if payload.is_monitored else 'paused'} for {clean_email}")
    return {"status": "success", "email": clean_email, "is_monitored": payload.is_monitored}


@app.post("/api/admin/users/delete")
def admin_delete_user(payload: AdminEmailPayload, request: Request):
    require_admin_write(request)
    admin_email = _get_admin_username()
    clean_email = payload.email.strip().lower()
    if not get_google_account(clean_email):
        raise HTTPException(status_code=404, detail="Account not found.")
    delete_google_account(clean_email)
    try:
        from backend.services.drive_state_adapter import _CACHE_LOCK, _STATE_CACHE, _PROCESSED_CACHE, _FILE_ID_CACHE
        with _CACHE_LOCK:
            _STATE_CACHE.pop(clean_email, None); _PROCESSED_CACHE.pop(clean_email, None); _FILE_ID_CACHE.pop(clean_email, None)
    except Exception:
        pass
    logger.warning(f"[ADMIN:{admin_email}] Disconnected account {clean_email}")
    if not list_all_google_accounts():
        stop_worker_internal()
    AdaptiveMemoryGovernor.trim()
    return {"status": "success", "email": clean_email}


@app.post("/api/admin/users/clear-reauth")
def admin_clear_reauth(payload: AdminEmailPayload, request: Request):
    require_admin_write(request)
    admin_email = _get_admin_username()
    clean_email = payload.email.strip().lower()
    if not get_google_account(clean_email):
        raise HTTPException(status_code=404, detail="Account not found.")
    clear_account_reauth(clean_email)
    logger.warning(f"[ADMIN:{admin_email}] Cleared reauth flag for {clean_email}")
    return {"status": "success", "email": clean_email}


@app.get("/api/admin/errors")
def admin_errors(request: Request, limit: int = 120):
    require_admin(request)
    requested = min(max(int(limit), 1), 500)

    entries = _read_error_ledger(requested)

    # Compatibility for errors generated before the new ledger existed.
    if not entries:
        legacy = _admin_log_tail(min(requested * 3, 600))
        entries = [
            {"ts": x.get("time", ""), "level": x.get("level", "INFO"), "message": x.get("message", "")}
            for x in legacy
            if x.get("level") in ("ERROR", "WARN", "WARNING")
        ]

    classified: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    descriptions: Dict[str, str] = {}

    for item in entries:
        level = str(item.get("level", "INFO")).upper()
        if level not in ("ERROR", "WARN", "WARNING"):
            continue
        cat, desc = _admin_error_category(item.get("message", ""))
        counts[cat] = counts.get(cat, 0) + 1
        descriptions[cat] = desc
        classified.append({
            **item,
            "time": item.get("time") or item.get("ts") or "",
            "category": cat,
        })

    ordered = [
        "Network / DNS",
        "Google OAuth / API",
        "AI / Groq",
        "Parsing / Validation",
        "Calendar / Event",
        "Attachment / Drive / OCR",
        "Worker / Processing",
        "Other",
    ]
    default_desc = {
        "Network / DNS": "Connectivity, DNS lookup, refused connections or timeouts.",
        "Google OAuth / API": "Google authentication, permissions or API failures.",
        "AI / Groq": "AI provider, quota, model or API-key failures.",
        "Parsing / Validation": "Malformed or unvalidated parser/model output.",
        "Calendar / Event": "Calendar creation, update, deletion or meeting metadata failures.",
        "Attachment / Drive / OCR": "Document extraction, OCR or Drive evidence failures.",
        "Worker / Processing": "Background monitor or historical inspection failures.",
        "Other": "Unclassified application error or warning.",
    }
    categories = [
        {"name": k, "count": counts.get(k, 0), "description": descriptions.get(k, default_desc[k])}
        for k in ordered
    ]

    return {
        "errors": classified[-requested:],
        "categories": categories,
        "total": len(classified),
        "source": "ema_errors.jsonl" if ERROR_LEDGER_FILE.exists() else "calendar_assistant.log",
    }


@app.get("/api/admin/logs")
def admin_logs(request: Request, limit: int = 180):
    require_admin(request)
    return {"lines": _admin_log_tail(min(max(limit, 1), 400))}


@app.get("/api/admin/activity")
def admin_activity(request: Request, limit: int = 100):
    require_admin(request)
    rows = []
    for acc in list_all_google_accounts():
        email = (acc.get("email") or "").strip().lower()
        try:
            for action in list_drive_emails(email, limit=30, offset=0):
                row = dict(action)
                row["email"] = email
                rows.append(row)
        except Exception:
            continue
    rows.sort(key=lambda x: x.get("processed_at", ""), reverse=True)
    return {"activity": rows[:min(max(limit, 1), 300)]}


@app.post("/api/admin/memory/trim")
def admin_memory_trim(request: Request):
    require_admin_write(request)
    admin_email = _get_admin_username()
    AdaptiveMemoryGovernor.trim(force=True)
    logger.info(f"[ADMIN:{admin_email}] Forced memory cleanup")
    return {"status": "success", "memory_mb": AdaptiveMemoryGovernor.get_memory_usage_mb()}


@app.post("/api/admin/worker/start")
def admin_worker_start(request: Request):
    require_admin_write(request)
    admin_email = _get_admin_username()
    start_worker_internal()
    logger.warning(f"[ADMIN:{admin_email}] Worker start requested")
    return {"status": "started"}


@app.post("/api/admin/worker/stop")
def admin_worker_stop(request: Request):
    require_admin_write(request)
    admin_email = _get_admin_username()
    stop_worker_internal()
    logger.warning(f"[ADMIN:{admin_email}] Worker stop requested")
    return {"status": "stopping"}


@app.post("/api/admin/inspect/start")
def admin_inspect_start(payload: AdminInspectPayload, request: Request):
    require_admin_write(request)
    admin_email = _get_admin_username()
    global INSPECT_THREAD
    monitored = [a for a in list_all_google_accounts() if a.get("is_monitored")]
    if not monitored:
        raise HTTPException(status_code=400, detail="No monitored accounts found.")
    if INSPECT_STATE["is_running"]:
        return {"status": "already_running"}
    count = min(max(10, int(payload.count)), 40)
    INSPECT_STATE["stop_event"].clear()
    INSPECT_THREAD = threading.Thread(target=inspect_previous_loop, args=(count, monitored), daemon=True)
    INSPECT_THREAD.start()
    logger.warning(f"[ADMIN:{admin_email}] Historical scan started for up to {count} email(s) across all monitored accounts")
    return {"status": "started", "target_count": count}


@app.post("/api/admin/inspect/stop")
def admin_inspect_stop(request: Request):
    require_admin_write(request)
    admin_email = _get_admin_username()
    if not INSPECT_STATE["is_running"]:
        return {"status": "not_running"}
    INSPECT_STATE["stop_event"].set()
    logger.warning(f"[ADMIN:{admin_email}] Historical scan stop requested")
    return {"status": "stopping"}


# ==============================================================================
# TEMPLATE HANDLERS
# ==============================================================================
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    fav_path = FRONTEND_DIR / "favicon.ico"
    if fav_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(fav_path)
    return Response(status_code=204)


@app.get("/logo.png", include_in_schema=False)
def logo():
    logo_path = FRONTEND_DIR / "logo.png"
    if logo_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(logo_path)
    return Response(status_code=204)


@app.get("/")
def root(request: Request):
    # Production Render service is API/auth only. Netlify owns the website.
    if os.getenv("EMA_ENV", "development").strip().lower() in {"production", "prod"}:
        return JSONResponse({"service": "EMA API", "status": "online", "frontend": getattr(config, "FRONTEND_URL", "")})

    sid = get_request_session_id(request)
    if sid and get_accounts_for_session(sid):
        return RedirectResponse(url="/index.html", status_code=303)
    return RedirectResponse(url="/login.html", status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
@app.get("/privacy.html", response_class=HTMLResponse)
def serve_privacy_page():
    privacy_path = FRONTEND_DIR / "privacy.html"
    if not privacy_path.exists():
        raise HTTPException(status_code=404, detail="privacy.html not found")
    with open(privacy_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/terms", response_class=HTMLResponse)
@app.get("/terms.html", response_class=HTMLResponse)
def serve_terms_page():
    terms_path = FRONTEND_DIR / "terms.html"
    if not terms_path.exists():
        raise HTTPException(status_code=404, detail="terms.html not found")
    with open(terms_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/index.html", response_class=HTMLResponse)
def serve_index_page(request: Request, session_id: Optional[str] = None):
    sid = session_id or get_request_session_id(request) or ""
    if not sid or not get_accounts_for_session(sid):
        return RedirectResponse(url="/login.html", status_code=303)

    html_path = FRONTEND_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html not found")
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    response = HTMLResponse(content=content)
    response.set_cookie(
        key="assistant_session_id",
        value=sid,
        httponly=True,
        secure=_session_cookie_secure(),
        samesite="lax",
        path="/",
        max_age=getattr(config, "SESSION_TTL_SECONDS", 7 * 24 * 3600),
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    # The next browser navigation can remove a session token from the visible URL.
    return response


@app.get("/login.html", response_class=HTMLResponse)
def serve_login_page(request: Request):
    sid = get_request_session_id(request)
    if sid and get_accounts_for_session(sid):
        return RedirectResponse(url=f"/index.html?session_id={sid}", status_code=303)

    html_path = FRONTEND_DIR / "login.html"
    if not html_path.exists():
        return HTMLResponse("<h2>Login page not found</h2>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ==============================================================================
# OAUTH FLOW
# ==============================================================================
def build_oauth_flow(request: Request, state: Optional[str] = None, redirect_uri: Optional[str] = None) -> Flow:
    target_redirect = redirect_uri or get_dynamic_redirect_uri(request)
    client_config = {
        "web": {
            "client_id": config.GOOGLE_CLIENT_ID.strip(),
            "client_secret": config.GOOGLE_CLIENT_SECRET.strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [
                target_redirect,
                "http://127.0.0.1:8000/auth/callback",
                "http://localhost:8000/auth/callback",
                "http://127.0.0.1:8000/api/auth/callback",
            ],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=target_redirect, state=state)


@app.post("/api/auth/launch-browser")
def launch_browser_auth(request: Request, email: Optional[str] = None):
    existing_sid = get_request_session_id(request)
    sid = existing_sid or secrets.token_urlsafe(32)
    hint_param = f"&email={email.strip().lower()}" if email else ""
    chrome_target = f"http://127.0.0.1:8000/auth/login?session_id={sid}&browser=1{hint_param}"
    open_in_chrome_or_default(chrome_target)

    response = JSONResponse({"status": "opened", "session_id": sid, "auth_url": chrome_target})
    response.set_cookie(key="assistant_session_id", value=sid, httponly=True, secure=_session_cookie_secure(), samesite="lax", path="/", max_age=getattr(config, "SESSION_TTL_SECONDS", 7 * 24 * 3600))
    return response


@app.get("/auth/login")
@app.get("/auth/google")
@app.get("/api/auth/google")
def auth_login(
    request: Request,
    session_id: Optional[str] = None,
    browser: Optional[str] = None,
    email: Optional[str] = None,
):
    existing_sid = get_request_session_id(request)
    existing_accounts = get_accounts_for_session(existing_sid) if existing_sid else []
    # OAuth state is an ephemeral flow identifier. For a first login, create a fresh server-side session token separately.
    sid = existing_sid if existing_accounts else secrets.token_urlsafe(32)
    locked_redirect_uri = get_dynamic_redirect_uri(request)

    flow = build_oauth_flow(request=request, state=sid, redirect_uri=locked_redirect_uri)

    auth_kwargs: Dict[str, Any] = {
        "state": sid,
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "false",
    }
    if email:
        auth_kwargs["login_hint"] = email.strip().lower()

    auth_url, _ = flow.authorization_url(**auth_kwargs)

    set_auth_flow(
        sid,
        status="pending",
        redirect_uri=locked_redirect_uri,
        code_verifier=flow.code_verifier or "",
        error=None,
    )

    if browser != "1":
        hint_param = f"&email={email.strip().lower()}" if email else ""
        chrome_target = f"http://127.0.0.1:8000/auth/login?session_id={sid}&browser=1{hint_param}"
        open_in_chrome_or_default(chrome_target)

        waiting_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><title>Connecting to Google...</title><script src="https://cdn.tailwindcss.com"></script></head>
        <body class="bg-[#060911] text-slate-200 min-h-screen flex items-center justify-center p-4 font-sans">
          <div class="max-w-md w-full p-8 rounded-3xl bg-slate-900 border border-blue-500/30 text-center space-y-4 shadow-2xl">
            <div id="spinner" class="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin mx-auto"></div>
            <h2 id="heading" class="text-base font-bold text-white">Browser Window Opened</h2>
            <p id="subtext" class="text-xs text-slate-400">Complete authentication in the opened browser window.</p>
            <div id="error-box" class="hidden p-3 bg-rose-950/80 border border-rose-500/30 rounded-2xl text-xs text-rose-300"></div>
            <button onclick="window.location.replace('/login.html')" class="mt-4 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 rounded-xl transition">Cancel</button>
          </div>
          <script>
            const timer = setInterval(async () => {{
              try {{
                const res = await fetch("/api/auth/status?session_id={sid}");
                const data = await res.json();
                if (data.status === "completed" || data.authenticated) {{
                  clearInterval(timer);
                  window.location.replace("/index.html?session_id={sid}");
                }} else if (data.status === "failed") {{
                  clearInterval(timer);
                  document.getElementById("spinner").classList.add("hidden");
                  document.getElementById("heading").innerText = "Sign-In Failed";
                  const errBox = document.getElementById("error-box");
                  errBox.innerText = data.error || "Authentication denied";
                  errBox.classList.remove("hidden");
                }}
              }} catch (e) {{}}
            }}, 1200);
          </script>
        </body>
        </html>
        """
        response = HTMLResponse(content=waiting_html)
        response.set_cookie(key="assistant_session_id", value=sid, httponly=True, secure=_session_cookie_secure(), samesite="lax", path="/", max_age=getattr(config, "SESSION_TTL_SECONDS", 7 * 24 * 3600))
        return response

    response = RedirectResponse(auth_url)
    response.set_cookie(key="assistant_session_id", value=sid, httponly=True, secure=_session_cookie_secure(), samesite="lax", path="/", max_age=getattr(config, "SESSION_TTL_SECONDS", 7 * 24 * 3600))
    return response


@app.get("/auth/callback")
@app.get("/api/auth/callback")
def auth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    session_id = state or get_request_session_id(request) or ""
    flow_record = get_auth_flow(session_id) if session_id else None

    if not session_id or not flow_record:
        return HTMLResponse("Authentication session expired. Please start sign-in again.", status_code=400)

    if error or not code:
        raw_err = error or "missing_code"
        safe_err = html.escape(str(raw_err))
        emit_user_log("ERROR", f"Google authentication failed: {safe_err}")
        if session_id:
            set_auth_flow(session_id, status="failed", error=safe_err)

        return HTMLResponse(
            f"""
            <!DOCTYPE html>
            <html lang="en">
            <head><meta charset="utf-8"><title>Authentication Failed</title><script src="https://cdn.tailwindcss.com"></script></head>
            <body class="bg-[#060911] text-slate-100 min-h-screen flex items-center justify-center p-4 font-sans">
              <div class="max-w-md w-full p-8 rounded-3xl bg-slate-900 border border-rose-500/30 text-center space-y-4 shadow-2xl">
                <div class="w-12 h-12 rounded-full bg-rose-500/10 text-rose-400 flex items-center justify-center mx-auto text-xl font-bold">✕</div>
                <h2 class="text-base font-bold text-white">Sign-in Unsuccessful</h2>
                <p class="text-xs text-slate-400">{safe_err}</p>
                <a href="/auth/login?browser=1" class="inline-block mt-3 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-xs font-semibold text-white rounded-xl transition">Try Again</a>
              </div>
            </body>
            </html>
            """,
            status_code=400,
        )

    try:
        redirect_uri = flow_record.get("redirect_uri") if flow_record else get_dynamic_redirect_uri(request)
        code_verifier = flow_record.get("code_verifier") if flow_record else None

        flow = build_oauth_flow(request=request, state=session_id, redirect_uri=redirect_uri)
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code, code_verifier=code_verifier or None)

        creds = flow.credentials
        creds_dict = json.loads(creds.to_json())

        creds_dict["client_id"] = config.GOOGLE_CLIENT_ID.strip()
        creds_dict["client_secret"] = config.GOOGLE_CLIENT_SECRET.strip()

        user_email = ""
        user_name = ""
        user_picture = ""

        if getattr(creds, "id_token", None):
            try:
                verified_payload = google_id_token.verify_oauth2_token(
                    creds.id_token,
                    GoogleRequest(),
                    config.GOOGLE_CLIENT_ID.strip(),
                )
                if verified_payload.get("email_verified") is False:
                    raise ValueError("Google account email is not verified.")
                user_email = verified_payload.get("email", "")
                user_name = verified_payload.get("name", "")
                user_picture = verified_payload.get("picture", "")
            except Exception as verify_err:
                logger.warning(f"Google ID token verification failed; using OAuth userinfo fallback: {verify_err}")

        if not user_email:
            try:
                from googleapiclient.discovery import build
                service = build("oauth2", "v2", credentials=creds, cache_discovery=False, static_discovery=False)
                ui = service.userinfo().get().execute()
                user_email = ui.get("email", "")
                user_name = ui.get("name", user_email)
                user_picture = ui.get("picture", "")
            except Exception:
                user_email = f"user_{session_id[:6]}@gmail.com"
                user_name = "User"

        clean_user_email = user_email.strip().lower()

        existing_session_accounts = get_accounts_for_session(session_id)
        authenticated_session_id = session_id if existing_session_accounts else secrets.token_urlsafe(32)
        if not existing_session_accounts and not create_user_session(authenticated_session_id):
            raise RuntimeError("Could not initialize secure user session.")

        # Do not persist OAuth client secrets; credentials are reconstructed from server environment on refresh.
        creds_dict.pop("client_secret", None)
        creds_dict.pop("client_id", None)

        if not creds_dict.get("refresh_token"):
            existing_acc = get_google_account(clean_user_email)
            if existing_acc and existing_acc.get("token_data"):
                try:
                    old_data = json.loads(existing_acc["token_data"])
                    if old_data.get("refresh_token"):
                        creds_dict["refresh_token"] = old_data["refresh_token"]
                except Exception:
                    pass

        upsert_google_account(
            email=clean_user_email,
            name=user_name or clean_user_email,
            picture=user_picture,
            token_data=json.dumps(creds_dict),
            session_id=authenticated_session_id,
            is_monitored=1,
        )

        clear_account_reauth(clean_user_email)
        if session_id:
            set_auth_flow(session_id, status="completed", email=clean_user_email, authenticated_session_id=authenticated_session_id, error=None)

        emit_user_log("SUCCESS", f"Connected account {clean_user_email} to monitoring.")
        focus_desktop_window()

        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><title>Authentication Successful</title><script src="https://cdn.tailwindcss.com"></script></head>
        <body class="bg-[#060911] text-slate-200 min-h-screen flex items-center justify-center p-4 font-sans">
          <div class="max-w-md w-full p-8 rounded-3xl bg-slate-900 border border-emerald-500/30 text-center space-y-4 shadow-2xl">
            <div class="w-14 h-14 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-center mx-auto text-2xl font-bold">✓</div>
            <div>
              <h2 class="text-base font-bold text-white tracking-tight">Account Connected!</h2>
              <p class="text-xs text-slate-400 mt-1">Authenticated <span class="text-blue-300 font-mono">{html.escape(clean_user_email)}</span></p>
            </div>
            <div class="p-3 bg-slate-950/80 border border-white/5 rounded-2xl text-xs text-emerald-400 font-medium">
              You can now close this browser window and return to EMA.
            </div>
          </div>
          <script>
            setTimeout(() => {{
              try {{ window.close(); }} catch (e) {{}}
              window.location.replace("/index.html");
            }}, 2000);
          </script>
        </body>
        </html>
        """
        frontend_url = (getattr(config, "FRONTEND_URL", "") or "").rstrip("/")
        if os.getenv("EMA_ENV", "development").strip().lower() in {"production", "prod"} and frontend_url:
            response = RedirectResponse(url=f"{frontend_url}/index.html", status_code=303)
        else:
            response = HTMLResponse(content=html_content)
        response.set_cookie(key="assistant_session_id", value=authenticated_session_id, httponly=True, secure=_session_cookie_secure(), samesite="lax", path="/", max_age=getattr(config, "SESSION_TTL_SECONDS", 7 * 24 * 3600))
        return response

    except Exception as e:
        err_msg = str(e)
        emit_user_log("ERROR", f"Account authentication failed: {err_msg}")
        if session_id:
            set_auth_flow(session_id, status="failed", error=err_msg)
        return HTMLResponse(
            f"""
            <!DOCTYPE html>
            <html lang="en">
            <head><meta charset="utf-8"><title>Authentication Failed</title><script src="https://cdn.tailwindcss.com"></script></head>
            <body class="bg-[#060911] text-slate-100 min-h-screen flex items-center justify-center p-4 font-sans">
              <div class="max-w-md w-full p-8 rounded-3xl bg-slate-900 border border-rose-500/30 text-center space-y-4 shadow-2xl">
                <div class="w-12 h-12 rounded-full bg-rose-500/10 text-rose-400 flex items-center justify-center mx-auto text-xl font-bold">✕</div>
                <h2 class="text-base font-bold text-white">Token Exchange Failed</h2>
                <p class="text-xs text-slate-400">{html.escape(err_msg)}</p>
                <a href="/auth/login?browser=1" class="inline-block mt-3 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-xs font-semibold text-white rounded-xl transition">Try Again</a>
              </div>
            </body>
            </html>
            """,
            status_code=400,
        )
    finally:
        AdaptiveMemoryGovernor.trim()


@app.get("/api/auth/status")
def get_auth_status(request: Request, session_id: Optional[str] = None):
    sid = session_id or get_request_session_id(request)
    flow = get_auth_flow(sid) if sid else None

    if flow:
        return {
            "status": flow.get("status", "pending"),
            "error": flow.get("error"),
            "email": flow.get("email"),
            "authenticated": flow.get("status") == "completed",
        }

    scoped = get_accounts_for_session(sid)
    if scoped:
        return {"status": "completed", "authenticated": True, "email": scoped[0]["email"], "error": None}

    return {"status": "idle", "authenticated": False, "error": None}


def _clear_session_cookie(response: Response):
    response.delete_cookie(key="assistant_session_id", path="/")


@app.get("/auth/logout")
@app.get("/api/auth/logout")
@app.get("/api/logout")
def auth_logout_get(request: Request):
    # GET is safe: clear the browser session only; account deletion requires POST.
    response = RedirectResponse(url="/login.html", status_code=303)
    _clear_session_cookie(response)
    return response


@app.post("/auth/logout")
@app.post("/api/auth/logout")
@app.post("/api/logout")
def auth_logout(request: Request):
    sid = get_request_session_id(request)

    if sid:
        to_delete = get_accounts_for_session(sid)
        for acc in to_delete:
            clean_email = acc.get("email", "").strip().lower()
            if clean_email:
                delete_google_account(clean_email)
                emit_user_log("INFO", f"Disconnected account: {clean_email}")

        try:
            from backend.services.drive_state_adapter import _CACHE_LOCK, _STATE_CACHE, _PROCESSED_CACHE, _FILE_ID_CACHE
            with _CACHE_LOCK:
                for acc in to_delete:
                    em = acc.get("email", "").strip().lower()
                    _STATE_CACHE.pop(em, None)
                    _PROCESSED_CACHE.pop(em, None)
                    _FILE_ID_CACHE.pop(em, None)
        except Exception:
            pass

        revoke_user_session(sid)

    remaining = list_all_google_accounts()
    if not remaining:
        stop_worker_internal()

    AdaptiveMemoryGovernor.trim(force=True)

    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
        or request.method == "POST"
    )

    if is_ajax:
        response = JSONResponse({
            "status": "success",
            "authenticated": False,
            "remaining": len(remaining),
            "redirect": "/login.html"
        })
    else:
        response = RedirectResponse(url="/login.html", status_code=303)

    _clear_session_cookie(response)
    return response


# ==============================================================================
# USER IDENTITY & PROFILE MANAGEMENT
# ==============================================================================
@app.get("/api/user")
def get_user(request: Request):
    sid = get_request_session_id(request)
    if not sid:
        return {"authenticated": False}

    scoped_accs = get_accounts_for_session(sid)
    if not scoped_accs:
        return {"authenticated": False}

    primary = scoped_accs[0]
    primary_email = primary["email"]

    try:
        creds = get_credentials_for_account(primary_email)
        if creds is None or is_reauth_needed(primary_email):
            return {
                "authenticated": False,
                "needs_reauth": True,
                "email": primary_email,
                "message": "Google credentials expired. Please re-authenticate.",
            }

        resolved_name, is_custom = resolve_user_display_name(scoped_accs, primary_email)
        target_cal_setting = get_drive_setting(primary_email, "target_calendar_account", "auto")

        accounts_summary = [
            {
                "email": a["email"],
                "name": a.get("name") or a["email"],
                "picture": a.get("picture") or "",
                "is_monitored": bool(a.get("is_monitored", 1))
            }
            for a in scoped_accs
        ]

        return {
            "authenticated": True,
            "email": primary_email,
            "name": resolved_name,
            "is_custom_name": is_custom,
            "picture": primary.get("picture"),
            "total_accounts": len(scoped_accs),
            "monitored_accounts": sum(1 for a in scoped_accs if a.get("is_monitored")),
            "target_calendar": target_cal_setting,
            "accounts": accounts_summary
        }
    except Exception as e:
        logger.error(f"Error checking user credentials: {e}")
        return {"authenticated": False}


class ProfileUpdatePayload(BaseModel):
    display_name: str


@app.post("/api/user/profile")
def update_user_profile(payload: ProfileUpdatePayload, request: Request):
    sid = get_request_session_id(request)
    scoped_accs = get_accounts_for_session(sid)
    if not scoped_accs:
        raise HTTPException(status_code=401, detail="Unauthorized: No active accounts found.")

    clean_name = payload.display_name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Display name cannot be empty.")

    for acc in scoped_accs:
        set_drive_setting(acc["email"], "custom_display_name", clean_name)

    emit_user_log("INFO", f"User profile display name updated to: '{clean_name}'")
    return {"status": "success", "display_name": clean_name}


@app.get("/api/accounts")
def list_accounts(request: Request):
    sid = get_request_session_id(request)
    if not sid:
        return {"accounts": []}

    accounts = get_accounts_for_session(sid)
    for a in accounts:
        acc_email = a["email"]
        health = get_account_auth_state(acc_email)
        a["needs_reauth"] = bool(health.get("needs_reauth") or (health.get("expired") and not health.get("has_refresh_token")))
        a["credential_expired"] = bool(health.get("expired"))
    return {"accounts": accounts}


class AccountTogglePayload(BaseModel):
    email: str
    is_monitored: bool


@app.post("/api/accounts/toggle")
def toggle_account_monitoring(payload: AccountTogglePayload, request: Request):
    sid = get_request_session_id(request)
    clean_email = payload.email.strip().lower()

    if not verify_account_ownership(clean_email, sid):
        raise HTTPException(status_code=403, detail="Unauthorized: Account does not belong to your session.")

    set_account_monitoring_status(clean_email, payload.is_monitored)
    status_label = "enabled" if payload.is_monitored else "paused"
    emit_user_log("INFO", f"Monitoring {status_label} for {clean_email}")
    return {"status": "success", "email": clean_email, "is_monitored": payload.is_monitored}


class AccountDeletePayload(BaseModel):
    email: str


@app.post("/api/accounts/delete")
def disconnect_account(payload: AccountDeletePayload, request: Request):
    sid = get_request_session_id(request)
    clean_email = payload.email.strip().lower()

    if not verify_account_ownership(clean_email, sid):
        raise HTTPException(status_code=403, detail="Unauthorized: Account does not belong to your session.")

    delete_google_account(clean_email)
    emit_user_log("INFO", f"Removed account: {clean_email}")
    remaining = list_all_google_accounts()
    if not remaining:
        stop_worker_internal()

    AdaptiveMemoryGovernor.trim()
    return {"status": "success", "email": clean_email, "remaining": len(remaining)}


# ==============================================================================
# SETTINGS & THEMES
# ==============================================================================
class SettingsPayload(BaseModel):
    only_remind_with_files: Optional[bool] = None
    design_events_enabled: Optional[bool] = None
    design_events_prompt: Optional[str] = None
    spam_protection_enabled: Optional[bool] = None
    spam_threshold: Optional[int] = None
    default_timing: Optional[str] = None
    theme_mode: Optional[str] = None
    target_calendar_account: Optional[str] = None


@app.get("/api/settings")
def get_app_settings(request: Request):
    sid = get_request_session_id(request)
    email = get_primary_email_for_session(sid)
    if not email:
        return {
            "only_remind_with_files": False,
            "design_events_enabled": False,
            "design_events_prompt": "",
            "spam_protection_enabled": True,
            "spam_threshold": 50,
            "default_timing": "09:00",
            "theme_mode": "dark",
            "target_calendar_account": "auto",
        }

    return {
        "only_remind_with_files": get_drive_setting(email, "only_remind_with_files", "false").lower() == "true",
        "design_events_enabled": get_drive_setting(email, "design_events_enabled", "false").lower() == "true",
        "design_events_prompt": get_drive_setting(email, "design_events_prompt", ""),
        "spam_protection_enabled": get_drive_setting(email, "spam_protection_enabled", "true").lower() == "true",
        "spam_threshold": int(get_drive_setting(email, "spam_threshold", "50")),
        "default_timing": get_drive_setting(email, "default_timing", "09:00"),
        "theme_mode": get_drive_setting(email, "theme_mode", "dark"),
        "target_calendar_account": get_drive_setting(email, "target_calendar_account", "auto"),
    }


@app.post("/api/settings")
def update_app_settings(payload: SettingsPayload, request: Request):
    sid = get_request_session_id(request)
    session_accs = get_accounts_for_session(sid)

    if not session_accs:
        raise HTTPException(status_code=401, detail="Unauthorized: No active accounts found for this session.")

    for acc in session_accs:
        acc_email = acc["email"].strip().lower()
        if payload.only_remind_with_files is not None:
            set_drive_setting(acc_email, "only_remind_with_files", str(payload.only_remind_with_files).lower())
        if payload.design_events_enabled is not None:
            set_drive_setting(acc_email, "design_events_enabled", str(payload.design_events_enabled).lower())
        if payload.design_events_prompt is not None:
            set_drive_setting(acc_email, "design_events_prompt", payload.design_events_prompt.strip()[:500])
        if payload.spam_protection_enabled is not None:
            set_drive_setting(acc_email, "spam_protection_enabled", str(payload.spam_protection_enabled).lower())
        if payload.spam_threshold is not None:
            clamped = max(10, min(95, payload.spam_threshold))
            set_drive_setting(acc_email, "spam_threshold", str(clamped))
        if payload.default_timing is not None:
            clean_time = payload.default_timing.strip()
            if not _valid_hhmm(clean_time):
                raise HTTPException(status_code=422, detail="default_timing must use HH:MM in 24-hour format.")
            set_drive_setting(acc_email, "default_timing", clean_time)
        if payload.theme_mode is not None:
            theme = payload.theme_mode.strip().lower()
            if theme not in {"dark", "read"}:
                raise HTTPException(status_code=422, detail="theme_mode must be 'dark' or 'read'.")
            set_drive_setting(acc_email, "theme_mode", theme)
        if payload.target_calendar_account is not None:
            target = payload.target_calendar_account.strip().lower()
            allowed_targets = {a["email"].strip().lower() for a in session_accs}
            if target not in allowed_targets and target != "auto":
                raise HTTPException(status_code=403, detail="Target calendar must belong to one of your connected accounts.")
            set_drive_setting(acc_email, "target_calendar_account", target or "auto")

    emit_user_log("INFO", "Settings updated and saved to Google Drive.")
    return {"status": "success"}


# ==============================================================================
# CALENDAR CRUD (VIEW, EDIT, DELETE)
# ==============================================================================
class EventUpdatePayload(BaseModel):
    event_id: str
    calendar_id: str
    summary: str
    description: Optional[str] = None
    start_time: str
    end_time: str
    location: Optional[str] = None


@app.post("/api/calendar/event/update")
def update_calendar_event(payload: EventUpdatePayload, request: Request):
    sid = get_request_session_id(request)
    clean_cal = payload.calendar_id.strip().lower()
    if not verify_account_ownership(clean_cal, sid):
        raise HTTPException(status_code=403, detail="Unauthorized calendar access.")

    try:
        from dateutil import parser as date_parser
        start_dt = date_parser.isoparse(payload.start_time.replace("Z", "+00:00"))
        end_dt = date_parser.isoparse(payload.end_time.replace("Z", "+00:00"))
        if end_dt <= start_dt:
            raise HTTPException(status_code=422, detail="end_time must be later than start_time.")
        _, calendar_service = get_google_services_for_account(clean_cal)
        event = calendar_service.events().get(calendarId=clean_cal, eventId=payload.event_id).execute()

        event["summary"] = payload.summary
        if payload.description is not None:
            event["description"] = payload.description
        if payload.location is not None:
            event["location"] = payload.location

        event["start"] = {"dateTime": payload.start_time} if "T" in payload.start_time else {"date": payload.start_time}
        event["end"] = {"dateTime": payload.end_time} if "T" in payload.end_time else {"date": payload.end_time}

        calendar_service.events().update(calendarId=clean_cal, eventId=payload.event_id, body=event).execute()
        emit_user_log("SUCCESS", f"Updated event '{payload.summary}' in {clean_cal}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error updating event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class EventDeletePayload(BaseModel):
    event_id: str
    calendar_id: str


@app.post("/api/calendar/event/delete")
def delete_calendar_event(payload: EventDeletePayload, request: Request):
    sid = get_request_session_id(request)
    clean_cal = payload.calendar_id.strip().lower()
    if not verify_account_ownership(clean_cal, sid):
        raise HTTPException(status_code=403, detail="Unauthorized calendar access.")

    try:
        _, calendar_service = get_google_services_for_account(clean_cal)
        calendar_service.events().delete(calendarId=clean_cal, eventId=payload.event_id).execute()
        emit_user_log("INFO", f"Deleted event ID {payload.event_id} from {clean_cal}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error deleting event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calendar/upcoming")
def get_upcoming_events(request: Request, max_events: int = 35, include_all: bool = True):
    sid = get_request_session_id(request)
    accounts = [a for a in get_accounts_for_session(sid) if a.get("is_monitored")]

    if not accounts:
        return {"events": []}

    all_events = []
    tz_name = getattr(config, "USER_TIMEZONE", "UTC")
    now = datetime.now(ZoneInfo(tz_name)).isoformat()
    clamped_max = min(max(5, max_events), 50)

    for acc in accounts:
        acc_email = acc["email"].strip().lower()
        try:
            _, calendar_service = get_google_services_for_account(acc_email)
            events_result = calendar_service.events().list(
                calendarId=acc_email,
                timeMin=now,
                maxResults=35,
                singleEvents=True,
                orderBy="startTime",
                fields="items(id,summary,description,location,start,end,htmlLink,attachments,extendedProperties)",
            ).execute()

            for ev in events_result.get("items", []):
                ev["_account_email"] = acc_email
                desc = ev.get("description", "") or ""
                summary = ev.get("summary", "") or ""

                is_assistant_event = (
                    "Smart Universal Email Assistant" in desc
                    or "Scheduled by" in desc
                    or "[AI Assistant]" in summary
                    or ev.get("extendedProperties", {}).get("private", {}).get("source") == "EmailAutomater"
                )
                ev["_is_assistant_created"] = is_assistant_event
                all_events.append(ev)
        except Exception as e:
            emit_user_log("ERROR", f"Could not fetch calendar events for {acc_email}: {str(e)}")

    all_events.sort(key=lambda x: x.get("start", {}).get("dateTime") or x.get("start", {}).get("date") or "")
    return {"events": all_events[:clamped_max]}


# ==============================================================================
# EMAILS API (ACCURATE CLASSIFICATION & EVENTS-ON-CALENDAR FILTERING)
# ==============================================================================
@app.get("/api/emails")
def list_emails(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    category_filter: str = "ALL",
    status_filter: Optional[str] = None,
    search: Optional[str] = None
):
    sid = get_request_session_id(request)
    session_accs = get_accounts_for_session(sid)

    all_emails = []
    clamped_limit = min(max(1, limit), 60)
    for acc in session_accs:
        try:
            records = list_drive_emails(acc["email"], category_filter="ALL", limit=80, offset=0)
            all_emails.extend(records)
        except Exception:
            pass

    # ACCURATE FILTERING ENGINE
    if category_filter == "EVENTS_ON_CALENDAR" or status_filter == "ACTIONED":
        all_emails = [
            e for e in all_emails
            if e.get("status") == "ACTIONED"
            or "scheduled" in str(e.get("action_taken", "")).lower()
        ]
    elif category_filter and category_filter != "ALL":
        all_emails = [e for e in all_emails if e.get("category") == category_filter]

    if status_filter and status_filter != "ALL" and category_filter != "EVENTS_ON_CALENDAR":
        all_emails = [e for e in all_emails if e.get("status") == status_filter]

    if search and search.strip():
        q = search.strip().lower()
        all_emails = [
            e for e in all_emails
            if q in e.get("subject", "").lower()
            or q in e.get("sender", "").lower()
            or q in e.get("summary", "").lower()
        ]

    all_emails.sort(key=lambda x: x.get("processed_at", ""), reverse=True)
    return {"emails": all_emails[offset:offset + clamped_limit], "total": len(all_emails)}


class InspectRequest(BaseModel):
    count: int


@app.post("/api/inspect/start")
def start_inspect(req: InspectRequest, request: Request):
    global INSPECT_THREAD
    sid = get_request_session_id(request)
    session_accs = [a for a in get_accounts_for_session(sid) if a.get("is_monitored")]

    if not session_accs:
        raise HTTPException(status_code=400, detail="No monitored accounts found for this session.")

    if INSPECT_STATE["is_running"]:
        return {"status": "already_running"}

    count = min(max(10, req.count), 40)
    INSPECT_STATE["stop_event"].clear()
    INSPECT_STATE["owner_session"] = sid
    INSPECT_THREAD = threading.Thread(target=inspect_previous_loop, args=(count, session_accs), daemon=True, name="EMA-Inspector")
    INSPECT_THREAD.start()
    return {"status": "started", "target_count": count}


@app.post("/api/inspect/stop")
def stop_inspect(request: Request):
    sid = get_request_session_id(request)
    if not sid:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not INSPECT_STATE["is_running"]:
        return {"status": "not_running"}
    if INSPECT_STATE.get("owner_session") != sid:
        raise HTTPException(status_code=403, detail="This inspection belongs to another session.")
    INSPECT_STATE["stop_event"].set()
    emit_user_log("INFO", "Email inspection canceled by user.")
    return {"status": "stopping"}


@app.get("/api/inspect/status")
def get_inspect_status(request: Request):
    sid = get_request_session_id(request)
    if not sid:
        raise HTTPException(status_code=401, detail="Authentication required.")
    owned = INSPECT_STATE.get("owner_session") == sid
    return {
        "is_running": bool(INSPECT_STATE["is_running"] and owned),
        "current_count": INSPECT_STATE["current_count"],
        "target_count": INSPECT_STATE["target_count"],
        "status_message": INSPECT_STATE["status_message"],
    }


@app.get("/api/stats")
def get_stats(request: Request):
    sid = get_request_session_id(request)
    session_accs = get_accounts_for_session(sid)

    total, actioned, processed, filtered, failed = 0, 0, 0, 0, 0
    for acc in session_accs:
        try:
            st = get_drive_stats(acc["email"])
            total += st.get("total", 0)
            actioned += st.get("actioned", 0)
            processed += st.get("processed", 0)
            filtered += st.get("filtered", 0)
            failed += st.get("failed", 0)
        except Exception:
            pass

    return {
        "summary": {
            "total": total,
            "actioned": actioned,
            "processed": processed,
            "filtered": filtered,
            "failed": failed,
            "time_saved": (total * 2),
        },
        "worker": {
            "is_running": WORKER_STATE["is_running"],
            "user_enabled": bool(session_accs) and all(_session_worker_enabled(a.get("session_id")) for a in session_accs),
            "status": WORKER_STATE["current_status"],
            "last_sync": WORKER_STATE["last_sync"],
        },
    }


@app.post("/api/worker/start")
def start_worker(request: Request):
    sid = get_request_session_id(request)
    accounts = get_accounts_for_session(sid)
    if not accounts:
        raise HTTPException(status_code=401, detail="Authentication required.")
    monitored = [a for a in accounts if a.get("is_monitored")]
    if not monitored:
        raise HTTPException(status_code=400, detail="No monitored accounts found for this session.")
    _set_session_worker_enabled(sid, True)
    start_worker_internal()
    return {"status": "started", "user_enabled": True}


@app.post("/api/worker/stop")
def stop_worker(request: Request):
    sid = get_request_session_id(request)
    accounts = get_accounts_for_session(sid)
    if not accounts:
        raise HTTPException(status_code=401, detail="Authentication required.")
    _set_session_worker_enabled(sid, False)
    # Stop the process only when no user's monitored accounts need the global worker.
    if not _has_any_active_worker_scope():
        stop_worker_internal()
    return {"status": "stopping", "user_enabled": False}


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=False), name="frontend")