import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("StateTracker")

try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None

_TOKEN_PREFIX = "enc:v1:"
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = getattr(config, "DB_PATH", BASE_DIR / "assistant_v2.db")
_INIT_LOCK = threading.RLock()
_INITIALIZED_DBS: set[str] = set()
_SESSION_TTL_SECONDS = int(getattr(config, "SESSION_TTL_SECONDS", 30 * 24 * 3600))
_SESSION_GRACE_MAX = 30 * 24 * 3600
_SESSION_TOUCH_CACHE: Dict[str, datetime] = {}
_SESSION_TOUCH_LOCK = threading.RLock()
_SESSION_TOUCH_INTERVAL = timedelta(seconds=60)
_SESSION_TOUCH_CACHE_MAX = 1024


def _get_fernet():
    if Fernet is None:
        return None
    raw = getattr(config, "TOKEN_ENCRYPTION_KEY", "") or ""
    if not raw:
        return None
    try:
        return Fernet(raw.encode("utf-8"))
    except Exception as exc:
        logger.error(f"Invalid TOKEN_ENCRYPTION_KEY configuration: {exc}")
        return None


def _encrypt_token_data(value: str) -> str:
    if not value:
        return value
    if value.startswith(_TOKEN_PREFIX):
        return value
    fernet = _get_fernet()
    if not fernet:
        if getattr(config, "TOKEN_ENCRYPTION_REQUIRED", False):
            raise RuntimeError("TOKEN_ENCRYPTION_REQUIRED is enabled but TOKEN_ENCRYPTION_KEY is unavailable or invalid.")
        return value
    return _TOKEN_PREFIX + fernet.encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_token_data(value: str) -> str:
    if not value:
        return value
    if not value.startswith(_TOKEN_PREFIX):
        return value
    fernet = _get_fernet()
    if not fernet:
        logger.error("Encrypted token_data exists but TOKEN_ENCRYPTION_KEY is unavailable.")
        return ""
    try:
        return fernet.decrypt(value[len(_TOKEN_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        logger.error("Failed to decrypt account credential data.")
        return ""


def get_db_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA cache_size=-2000;")
    conn.execute("PRAGMA wal_autocheckpoint=1000;")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH, force: bool = False):
    db_key = str(db_path.resolve())
    with _INIT_LOCK:
        if not force and db_key in _INITIALIZED_DBS and db_path.exists():
            return
        conn = get_db_connection(db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS google_accounts (
                    email TEXT PRIMARY KEY COLLATE NOCASE,
                    name TEXT,
                    picture TEXT,
                    token_data TEXT,
                    is_monitored INTEGER DEFAULT 1,
                    created_at TEXT,
                    session_id TEXT
                )
            """)
            cur.execute("PRAGMA table_info(google_accounts)")
            columns = {row[1] for row in cur.fetchall()}
            if "session_id" not in columns:
                cur.execute("ALTER TABLE google_accounts ADD COLUMN session_id TEXT")
            # Never delete user history during startup. Legacy history tables are retained
            # and may be migrated explicitly by a dedicated, backup-backed migration.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_hash TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON user_sessions (expires_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_accounts_email_nocase ON google_accounts (email COLLATE NOCASE)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_accounts_session ON google_accounts (session_id)")
            conn.commit()
            _INITIALIZED_DBS.add(db_key)
        finally:
            conn.close()



def _session_hash(session_id: str) -> str:
    return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()


def create_user_session(session_id: str, ttl_seconds: Optional[int] = None, db_path: Path = DEFAULT_DB_PATH) -> bool:
    clean = str(session_id or "").strip()
    # Real EMA browser sessions are long opaque tokens. A slightly lower minimum
    # keeps legacy/test-created account sessions migratable without allowing empty
    # or trivially short identifiers. New sessions are still generated at 32+ chars.
    if len(clean) < 8 or len(clean) > 256:
        return False
    ttl = max(900, min(int(ttl_seconds or _SESSION_TTL_SECONDS), _SESSION_GRACE_MAX))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires = now + timedelta(seconds=ttl)
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_sessions (session_hash, created_at, expires_at, last_seen) VALUES (?, ?, ?, ?)",
            (_session_hash(clean), now.isoformat(), expires.isoformat(), now.isoformat()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def is_user_session_valid(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    clean = str(session_id or "").strip()
    if not clean:
        return False
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        key = _session_hash(clean)
        row = conn.execute("SELECT expires_at FROM user_sessions WHERE session_hash = ?", (key,)).fetchone()
        if not row:
            return False
        try:
            valid = datetime.fromisoformat(row[0]) > datetime.now(timezone.utc).replace(tzinfo=None)
        except Exception:
            valid = False
        if not valid:
            # Keep expired session rows while an account still references the raw
            # session ID. Otherwise ensure_legacy_session could mistake an expired
            # session for a pre-migration session and silently revive it.
            linked_account = conn.execute(
                "SELECT 1 FROM google_accounts WHERE session_id = ? LIMIT 1",
                (clean,),
            ).fetchone()
            if not linked_account:
                conn.execute("DELETE FROM user_sessions WHERE session_hash = ?", (key,))
                conn.commit()
        return valid
    finally:
        conn.close()


def touch_user_session(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    clean = str(session_id or "").strip()
    if not is_user_session_valid(clean, db_path):
        return False

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    key = _session_hash(clean)
    with _SESSION_TOUCH_LOCK:
        previous = _SESSION_TOUCH_CACHE.get(key)
        if previous and now - previous < _SESSION_TOUCH_INTERVAL:
            return True
        _SESSION_TOUCH_CACHE[key] = now
        if len(_SESSION_TOUCH_CACHE) > _SESSION_TOUCH_CACHE_MAX:
            oldest = sorted(_SESSION_TOUCH_CACHE.items(), key=lambda item: item[1])
            for stale_key, _ in oldest[: len(_SESSION_TOUCH_CACHE) - _SESSION_TOUCH_CACHE_MAX]:
                _SESSION_TOUCH_CACHE.pop(stale_key, None)

    conn = get_db_connection(db_path)
    try:
        # Sliding expiration: active users keep their sign-in across browser restarts
        # while the configured inactivity/session lifetime is continuously renewed.
        ttl = max(900, min(int(_SESSION_TTL_SECONDS), _SESSION_GRACE_MAX))
        expires = now + timedelta(seconds=ttl)
        conn.execute(
            "UPDATE user_sessions SET last_seen = ?, expires_at = ? WHERE session_hash = ?",
            (now.isoformat(), expires.isoformat(), key),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def revoke_user_session(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    clean = str(session_id or "").strip()
    if not clean:
        return
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        session_key = _session_hash(clean)
        linked_account = conn.execute(
            "SELECT 1 FROM google_accounts WHERE session_id = ? LIMIT 1",
            (clean,),
        ).fetchone()
        if linked_account:
            # Keep an expired tombstone while an account still references this
            # session ID. Deleting the row would make a later legacy migration
            # mistake the revoked session for an old pre-migration session.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            expired = (now - timedelta(seconds=1)).isoformat()
            conn.execute(
                """
                INSERT INTO user_sessions (session_hash, created_at, expires_at, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_hash) DO UPDATE SET
                    expires_at = excluded.expires_at,
                    last_seen = excluded.last_seen
                """,
                (session_key, now.isoformat(), expired, now.isoformat()),
            )
        else:
            conn.execute("DELETE FROM user_sessions WHERE session_hash = ?", (session_key,))
        conn.execute(
            "DELETE FROM system_settings WHERE key = ?",
            (f"worker_enabled:{session_key}",),
        )
        conn.commit()
    finally:
        conn.close()


def is_user_session_worker_enabled(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Read the persisted per-session worker preference; default new sessions to enabled."""
    clean = str(session_id or "").strip()
    if not clean or len(clean) > 256:
        return False
    key = f"worker_enabled:{_session_hash(clean)}"
    try:
        init_db(db_path)
        conn = get_db_connection(db_path)
        try:
            row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
            if not row:
                return True
            return str(row[0]).strip().lower() in {"1", "true", "yes", "on"}
        finally:
            conn.close()
    except Exception as exc:
        # Fail closed if the durable preference cannot be read.
        logger.warning("Could not read persisted worker setting: %s", exc)
        return False


def set_user_session_worker_enabled(
    session_id: str, enabled: bool, db_path: Path = DEFAULT_DB_PATH
) -> bool:
    """Persist worker enablement independently of the current Python process."""
    clean = str(session_id or "").strip()
    if len(clean) < 8 or len(clean) > 256:
        return False
    key = f"worker_enabled:{_session_hash(clean)}"
    try:
        init_db(db_path)
        conn = get_db_connection(db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)",
                (key, "true" if enabled else "false"),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Could not persist worker setting: %s", exc)
        return False


def count_active_user_sessions(
    window_seconds: int = 300, db_path: Path = DEFAULT_DB_PATH
) -> int:
    """Count authenticated sessions seen recently, rather than relying on volatile RAM."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window = max(60, min(int(window_seconds), 86400))
    cutoff = now - timedelta(seconds=window)
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM user_sessions WHERE last_seen >= ? AND expires_at > ?",
            (cutoff.isoformat(), now.isoformat()),
        ).fetchone()
        return max(0, int(row[0] if row else 0))
    finally:
        conn.close()


def ensure_legacy_session(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Migrate pre-session-table accounts only; never recreate a known expired session."""
    clean = str(session_id or "").strip()
    if not clean:
        return False
    if is_user_session_valid(clean, db_path):
        return True
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        # A row that exists but is expired or malformed is a revoked/expired
        # session, not a legacy session eligible for migration.
        session_row = conn.execute(
            "SELECT expires_at FROM user_sessions WHERE session_hash = ?",
            (_session_hash(clean),),
        ).fetchone()
        if session_row:
            return False
        account_row = conn.execute(
            "SELECT 1 FROM google_accounts WHERE session_id = ? LIMIT 1",
            (clean,),
        ).fetchone()
    finally:
        conn.close()
    return create_user_session(clean, db_path=db_path) if account_row else False


def upsert_google_account(
    email: str,
    name: str,
    picture: str,
    token_data: str,
    session_id: Optional[str] = None,
    is_monitored: int = 1,
    db_path: Path = DEFAULT_DB_PATH,
):
    clean_email = str(email or "").strip().lower()
    if not clean_email:
        raise ValueError("Account email cannot be empty.")
    init_db(db_path)
    encrypted = _encrypt_token_data(token_data)
    conn = get_db_connection(db_path)
    try:
        now = datetime.now().isoformat()
        conn.execute("""
            INSERT INTO google_accounts (email, name, picture, token_data, is_monitored, created_at, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name,
                picture = excluded.picture,
                token_data = excluded.token_data,
                is_monitored = excluded.is_monitored,
                session_id = COALESCE(excluded.session_id, google_accounts.session_id)
        """, (clean_email, name, picture, encrypted, 1 if is_monitored else 0, now, session_id))
        conn.commit()
    finally:
        conn.close()


def list_all_google_accounts(session_id: Optional[str] = None, db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        query = "SELECT email, name, picture, is_monitored, created_at, session_id FROM google_accounts"
        params: tuple[Any, ...] = ()
        if session_id:
            query += " WHERE session_id = ?"
            params = (session_id,)
        query += " ORDER BY created_at ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_google_account(email: str, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    if not email:
        return None
    clean_email = email.strip().lower()
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM google_accounts WHERE email = ? COLLATE NOCASE", (clean_email,)).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("token_data"):
            result["token_data"] = _decrypt_token_data(result["token_data"])
        return result
    finally:
        conn.close()


def migrate_token_data_to_encrypted(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Encrypt existing plaintext credential blobs when an encryption key is configured."""
    fernet = _get_fernet()
    if not fernet:
        return 0
    init_db(db_path)
    conn = get_db_connection(db_path)
    migrated = 0
    try:
        rows = conn.execute("SELECT email, token_data FROM google_accounts WHERE token_data IS NOT NULL").fetchall()
        for row in rows:
            token_data = row[1] or ""
            if token_data.startswith(_TOKEN_PREFIX) or not token_data:
                continue
            encrypted = _encrypt_token_data(token_data)
            conn.execute("UPDATE google_accounts SET token_data = ? WHERE email = ? COLLATE NOCASE", (encrypted, row[0]))
            migrated += 1
        conn.commit()
    finally:
        conn.close()
    return migrated


def set_account_monitoring_status(email: str, is_monitored: bool, db_path: Path = DEFAULT_DB_PATH):
    clean_email = email.strip().lower()
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute("UPDATE google_accounts SET is_monitored = ? WHERE email = ? COLLATE NOCASE", (1 if is_monitored else 0, clean_email))
        conn.commit()
    finally:
        conn.close()


def delete_google_account(email: str, db_path: Path = DEFAULT_DB_PATH):
    clean_email = email.strip().lower()
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute("DELETE FROM google_accounts WHERE email = ? COLLATE NOCASE", (clean_email,))
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str = "", db_path: Path = DEFAULT_DB_PATH) -> str:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str, db_path: Path = DEFAULT_DB_PATH):
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    finally:
        conn.close()


def delete_setting(key: str, db_path: Path = DEFAULT_DB_PATH):
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute("DELETE FROM system_settings WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()
