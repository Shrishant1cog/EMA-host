import io
import json
import copy
import logging
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

from backend.services.google_auth import (
    get_drive_service_for_account,
    mark_account_needs_reauth,
    clear_account_reauth,
    is_reauth_needed
)

logger = logging.getLogger("DriveStateAdapter")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_FILENAME = "assistant_state.json"

DEFAULT_STATE_TEMPLATE: Dict[str, Any] = {
    "version": 1,
    "stats": {
        "total": 0,
        "actioned": 0,
        "processed": 0,
        "filtered": 0,
        "failed": 0
    },
    "settings": {
        "only_remind_with_files": "false",
        "design_events_enabled": "false",
        "design_events_prompt": "",
        "spam_protection_enabled": "true",
        "spam_threshold": "50",
        "default_timing": "09:00",
        "worker_enabled": "true",
        "last_sync_epoch": "0",
        "theme_mode": "dark",
        "target_calendar_account": "auto",
        "custom_display_name": ""
    },
    "email_actions": [],
    "processed_email_ids": [],
    "failed_email_ids": []
}

def _create_fresh_default_state() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULT_STATE_TEMPLATE)

_CACHE_LOCK = threading.RLock()
_STATE_CACHE: Dict[str, Dict[str, Any]] = {}
_PROCESSED_CACHE: Dict[str, Set[str]] = {}
_FILE_ID_CACHE: Dict[str, str] = {}
_STATE_LOADED_FROM_DRIVE: Dict[str, bool] = {}
_COMMIT_LOCKS: Dict[str, threading.RLock] = {}
_COMMIT_LOCKS_GUARD = threading.RLock()
_PROCESSED_ID_LIMIT = 5000


def _get_commit_lock(email: str) -> threading.RLock:
    clean = str(email or "").strip().lower()
    with _COMMIT_LOCKS_GUARD:
        if clean not in _COMMIT_LOCKS:
            _COMMIT_LOCKS[clean] = threading.RLock()
        return _COMMIT_LOCKS[clean]


def _get_shadow_path(email: str) -> Path:
    safe_name = "".join(c if c.isalnum() else "_" for c in email.lower())
    return BASE_DIR / f".state_shadow_{safe_name}.json"


def _load_local_shadow(email: str) -> Optional[Dict[str, Any]]:
    path = _get_shadow_path(email)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return None


def _save_local_shadow(email: str, state: Dict[str, Any]):
    path = _get_shadow_path(email)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, separators=(',', ':'))
    except Exception as e:
        logger.debug(f"Shadow save error for {email}: {e}")


def _get_or_create_state_file_id(drive_service, email: str) -> str:
    with _CACHE_LOCK:
        if email in _FILE_ID_CACHE:
            return _FILE_ID_CACHE[email]

    try:
        response = drive_service.files().list(
            spaces="appDataFolder",
            q=f"name = '{STATE_FILENAME}' and trashed = false",
            fields="files(id, name)",
            pageSize=1
        ).execute()
        files = response.get("files", [])

        if files:
            file_id = files[0]["id"]
            with _CACHE_LOCK:
                _FILE_ID_CACHE[email] = file_id
            return file_id

        file_metadata = {
            "name": STATE_FILENAME,
            "parents": ["appDataFolder"]
        }
        initial_payload = _create_fresh_default_state()
        raw_data = json.dumps(initial_payload, separators=(',', ':')).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(raw_data), mimetype="application/json", resumable=True)

        created_file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()

        file_id = created_file.get("id")
        with _CACHE_LOCK:
            _FILE_ID_CACHE[email] = file_id
        logger.info(f"Initialized private {STATE_FILENAME} in Google Drive for {email}")
        return file_id

    except HttpError as http_err:
        status = http_err.resp.status if hasattr(http_err, 'resp') else 0
        if status in (401, 403):
            mark_account_needs_reauth(email)
        raise http_err


def load_drive_state(email: str, force_refresh: bool = False) -> Dict[str, Any]:
    """Reads state with dual-persistence: local shadow cache + Google Drive merge."""
    if not email or not email.strip():
        return _create_fresh_default_state()

    clean_email = email.strip().lower()

    with _CACHE_LOCK:
        if not force_refresh and clean_email in _STATE_CACHE and _STATE_LOADED_FROM_DRIVE.get(clean_email):
            return _STATE_CACHE[clean_email]

    shadow_state = _load_local_shadow(clean_email)
    with _CACHE_LOCK:
        if clean_email not in _STATE_CACHE and shadow_state:
            _STATE_CACHE[clean_email] = shadow_state

    try:
        drive_service = get_drive_service_for_account(clean_email)
        file_id = _get_or_create_state_file_id(drive_service, clean_email)

        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        fh.seek(0)
        content = fh.read().decode("utf-8")
        remote_state = json.loads(content) if content.strip() else _create_fresh_default_state()

        with _CACHE_LOCK:
            local_state = _STATE_CACHE.get(clean_email, shadow_state or _create_fresh_default_state())
            merged = copy.deepcopy(remote_state)

            remote_stats = remote_state.get("stats", {})
            local_stats = local_state.get("stats", {})
            merged["stats"] = {
                "total": max(remote_stats.get("total", 0), local_stats.get("total", 0)),
                "actioned": max(remote_stats.get("actioned", 0), local_stats.get("actioned", 0)),
                "processed": max(remote_stats.get("processed", 0), local_stats.get("processed", 0)),
                "filtered": max(remote_stats.get("filtered", 0), local_stats.get("filtered", 0)),
                "failed": max(remote_stats.get("failed", 0), local_stats.get("failed", 0)),
            }

            if merged["stats"]["total"] == 0 and remote_state.get("email_actions"):
                acts = remote_state["email_actions"]
                merged["stats"]["total"] = len(acts)
                merged["stats"]["actioned"] = sum(1 for a in acts if isinstance(a, dict) and a.get("status") == "ACTIONED")
                merged["stats"]["processed"] = sum(1 for a in acts if isinstance(a, dict) and a.get("status") == "PROCESSED")
                merged["stats"]["filtered"] = sum(1 for a in acts if isinstance(a, dict) and a.get("status") == "FILTERED")
                merged["stats"]["failed"] = sum(1 for a in acts if isinstance(a, dict) and a.get("status") == "FAILED")

            remote_settings = remote_state.get("settings", {})
            local_settings = local_state.get("settings", {})
            merged["settings"] = {**local_settings, **remote_settings}

            seen_ids = set()
            merged_actions = []
            for a in (local_state.get("email_actions", []) + remote_state.get("email_actions", [])):
                if isinstance(a, dict) and a.get("email_id"):
                    mid = str(a["email_id"])
                    if mid not in seen_ids:
                        seen_ids.add(mid)
                        merged_actions.append(a)

            merged_actions.sort(key=lambda x: x.get("processed_at", ""), reverse=True)
            merged["email_actions"] = merged_actions[:80]

            processed_ids = []
            processed_seen = set()
            for mid in (local_state.get("processed_email_ids", []) + remote_state.get("processed_email_ids", [])):
                if mid and str(mid) not in processed_seen:
                    processed_seen.add(str(mid))
                    processed_ids.append(str(mid))
            failed_ids = {str(x) for x in (local_state.get("failed_email_ids", []) + remote_state.get("failed_email_ids", [])) if x}
            for action in merged_actions:
                mid = action.get("email_id") if isinstance(action, dict) else None
                status = str(action.get("status", "PROCESSED")).upper() if isinstance(action, dict) else "PROCESSED"
                if mid and status == "FAILED":
                    failed_ids.add(str(mid))
                elif mid and str(mid) not in processed_seen:
                    processed_seen.add(str(mid))
                    processed_ids.append(str(mid))
            processed_ids = [mid for mid in processed_ids if mid not in failed_ids]
            merged["processed_email_ids"] = processed_ids[-_PROCESSED_ID_LIMIT:]
            merged["failed_email_ids"] = list(failed_ids)[-_PROCESSED_ID_LIMIT:]

            _STATE_CACHE[clean_email] = merged
            _STATE_LOADED_FROM_DRIVE[clean_email] = True
            _PROCESSED_CACHE[clean_email] = set(merged["processed_email_ids"])

        _save_local_shadow(clean_email, merged)
        clear_account_reauth(clean_email)
        return merged

    except Exception as e:
        logger.warning(f"Drive state sync error for {clean_email} ({e}). Retaining cached/shadow data.")
        with _CACHE_LOCK:
            if clean_email in _STATE_CACHE:
                return _STATE_CACHE[clean_email]
            if shadow_state:
                _STATE_CACHE[clean_email] = shadow_state
                return shadow_state
            fresh = _create_fresh_default_state()
            _STATE_CACHE[clean_email] = fresh
            return fresh


def commit_drive_state(email: str) -> bool:
    """Flush cached state using a per-account lock so concurrent writers cannot overwrite each other."""
    if not email or not email.strip():
        return False
    clean_email = email.strip().lower()
    with _get_commit_lock(clean_email):
        with _CACHE_LOCK:
            state = _STATE_CACHE.get(clean_email)
            if not state:
                return False
            stats = state.get("stats", {})
            actions = state.get("email_actions", [])
            if stats.get("total", 0) == 0 and not actions and not _STATE_LOADED_FROM_DRIVE.get(clean_email):
                logger.warning(f"Refusing to overwrite Drive for {clean_email} with uninitialized 0-state.")
                return False
            try:
                raw_data = json.dumps(state, separators=(",", ":")).encode("utf-8")
                snapshot = copy.deepcopy(state)
            except Exception as ser_err:
                logger.error(f"State serialization failure for {clean_email}: {ser_err}")
                return False

        _save_local_shadow(clean_email, snapshot)
        try:
            drive_service = get_drive_service_for_account(clean_email)
            file_id = _get_or_create_state_file_id(drive_service, clean_email)
            media = MediaIoBaseUpload(io.BytesIO(raw_data), mimetype="application/json")
            drive_service.files().update(fileId=file_id, media_body=media).execute()
            return True
        except Exception as e:
            logger.warning(f"Could not commit state to Google Drive for {clean_email}: {e}")
            return False

def is_email_processed_drive(email: str, email_id: str) -> bool:
    if not email or not email.strip():
        return False

    clean_email = email.strip().lower()
    str_mid = str(email_id)
    with _CACHE_LOCK:
        if clean_email in _PROCESSED_CACHE:
            return str_mid in _PROCESSED_CACHE[clean_email]

    load_drive_state(clean_email)
    with _CACHE_LOCK:
        return str_mid in _PROCESSED_CACHE.get(clean_email, set())


def record_email_action_drive(account_email: str, action_data: Dict[str, Any]):
    """Record processing outcome with correct status deltas and a bounded persistent processed-ID index."""
    if not account_email or not account_email.strip():
        return
    clean_email = account_email.strip().lower()
    state = load_drive_state(clean_email)
    mid = str(action_data.get("email_id", ""))

    with _CACHE_LOCK:
        actions = state.setdefault("email_actions", [])
        stats = state.setdefault("stats", {"total": 0, "actioned": 0, "processed": 0, "filtered": 0, "failed": 0})
        for key in ("total", "actioned", "processed", "filtered", "failed"):
            try:
                stats[key] = int(stats.get(key, 0) or 0)
            except (TypeError, ValueError):
                stats[key] = 0

        def bucket(status: str) -> str:
            if status == "ACTIONED":
                return "actioned"
            if status == "FILTERED":
                return "filtered"
            if status == "FAILED":
                return "failed"
            return "processed"

        new_status = str(action_data.get("status", "PROCESSED")).upper()
        existing_idx = next((i for i, a in enumerate(actions) if str(a.get("email_id")) == mid), None)
        if existing_idx is None:
            actions.insert(0, action_data)
            stats["total"] += 1
            stats[bucket(new_status)] += 1
        else:
            old_status = str(actions[existing_idx].get("status", "PROCESSED")).upper()
            if old_status != new_status:
                stats[bucket(old_status)] = max(0, stats[bucket(old_status)] - 1)
                stats[bucket(new_status)] += 1
            actions[existing_idx] = action_data

        state["email_actions"] = actions[:80]
        processed = [str(x) for x in state.setdefault("processed_email_ids", []) if str(x) != mid]
        failed_ids_state = [str(x) for x in state.setdefault("failed_email_ids", []) if str(x) != mid]
        if mid and new_status != "FAILED":
            processed.append(mid)
            state["processed_email_ids"] = processed[-_PROCESSED_ID_LIMIT:]
            _PROCESSED_CACHE.setdefault(clean_email, set()).add(mid)
            _PROCESSED_CACHE[clean_email].intersection_update(set(state["processed_email_ids"]))
        else:
            state["processed_email_ids"] = processed[-_PROCESSED_ID_LIMIT:]
            if mid:
                failed_ids_state.append(mid)
            state["failed_email_ids"] = failed_ids_state[-_PROCESSED_ID_LIMIT:]
            _PROCESSED_CACHE.setdefault(clean_email, set()).discard(mid)

    commit_drive_state(clean_email)

def get_drive_stats(email: str) -> Dict[str, int]:
    """Retrieves persistent lifetime stats directly from Google Drive state."""
    if not email or not email.strip():
        return {"total": 0, "actioned": 0, "processed": 0, "filtered": 0}

    clean_email = email.strip().lower()
    state = load_drive_state(clean_email)
    with _CACHE_LOCK:
        stats = state.get("stats", {})
        actions = state.get("email_actions", [])

        total = stats.get("total", 0)
        actioned = stats.get("actioned", 0)
        processed = stats.get("processed", 0)
        filtered = stats.get("filtered", 0)
        failed = stats.get("failed", 0)

        if total == 0 and len(actions) > 0:
            total = len(actions)
            actioned = sum(1 for a in actions if isinstance(a, dict) and a.get("status") == "ACTIONED")
            processed = sum(1 for a in actions if isinstance(a, dict) and a.get("status") == "PROCESSED")
            filtered = sum(1 for a in actions if isinstance(a, dict) and a.get("status") == "FILTERED")
            failed = sum(1 for a in actions if isinstance(a, dict) and a.get("status") == "FAILED")
            state["stats"] = {"total": total, "actioned": actioned, "processed": processed, "filtered": filtered, "failed": failed}

    return {"total": total, "actioned": actioned, "processed": processed, "filtered": filtered, "failed": failed}


def list_drive_emails(email: str, category_filter: str = "ALL", limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    if not email or not email.strip():
        return []

    clean_email = email.strip().lower()
    state = load_drive_state(clean_email)
    with _CACHE_LOCK:
        actions = state.get("email_actions", [])
        if category_filter and category_filter != "ALL":
            filtered = [a for a in actions if isinstance(a, dict) and a.get("category") == category_filter]
        else:
            filtered = [a for a in actions if isinstance(a, dict)]
        return filtered[offset:offset + limit]


def get_drive_setting(email: str, key: str, default: str = "") -> str:
    if not email or not email.strip():
        template_val = DEFAULT_STATE_TEMPLATE.get("settings", {}).get(key, "")
        return default if default else template_val

    clean_email = email.strip().lower()
    state = load_drive_state(clean_email)
    with _CACHE_LOCK:
        val = str(state.get("settings", {}).get(key, ""))
        if val:
            return val

    template_val = DEFAULT_STATE_TEMPLATE.get("settings", {}).get(key, "")
    return default if default else template_val


def set_drive_setting(email: str, key: str, value: str):
    if not email or not email.strip():
        return

    clean_email = email.strip().lower()
    state = load_drive_state(clean_email)
    with _CACHE_LOCK:
        state.setdefault("settings", {})[key] = str(value)
    commit_drive_state(clean_email)