import os
import sys
from pathlib import Path
from typing import List
from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
    if BASE_DIR.name == "backend":
        BASE_DIR = BASE_DIR.parent

# Local development should be deterministic: the project's .env wins over stale
# process-level values. Production keeps deployment environment variables as the
# source of truth. Set EMA_ENV=production in deployed environments.
_ENV_MODE = os.getenv('EMA_ENV', 'development').strip().lower()
_LOCAL_ENV_MODE = _ENV_MODE in {'dev', 'development', 'local'}

CANDIDATE_ENV_PATHS = [
    BASE_DIR / ".env",
    Path.cwd() / ".env",
    Path(os.path.expandvars(r"%LocalAppData%\Programs\EmailAutomater\.env")),
    Path(os.path.expandvars(r"%LocalAppData%\EmailAutomater\.env")),
    BASE_DIR.parent / ".env",
]

ENV_LOADED = False
for env_path in CANDIDATE_ENV_PATHS:
    if env_path.is_file():
        load_dotenv(env_path, override=_LOCAL_ENV_MODE)
        ENV_LOADED = True
        break
if not ENV_LOADED:
    load_dotenv(override=_LOCAL_ENV_MODE)


def _clean_env_val(key: str, default: str = "") -> str:
    val = os.getenv(key, default)
    if val is None:
        return default
    return val.strip().strip("'\"`")


def _env_bool(key: str, default: bool) -> bool:
    return _clean_env_val(key, str(default)).lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(_clean_env_val(key, str(default)))))
    except ValueError:
        return default


def _env_float(key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(_clean_env_val(key, str(default)))))
    except ValueError:
        return default


_detected_render_url = _clean_env_val("RENDER_EXTERNAL_URL")
BACKEND_URL: str = (_detected_render_url or _clean_env_val("BACKEND_URL", "http://127.0.0.1:8000")).rstrip("/")
FRONTEND_URL: str = _clean_env_val("FRONTEND_URL", "http://localhost:5500").rstrip("/")
REDIRECT_URI: str = _clean_env_val("REDIRECT_URI", f"{BACKEND_URL}/auth/callback")


def _parse_groq_keys() -> List[str]:
    raw = _clean_env_val("GROQ_API_KEYS")
    if raw:
        keys = [x.strip().strip("'\"`") for x in raw.split(",") if x.strip().strip("'\"`")]
        if keys:
            return keys
    single = _clean_env_val("GROQ_API_KEY")
    return [single] if single else []


GROQ_API_KEYS: List[str] = _parse_groq_keys()
GROQ_API_KEY: str = GROQ_API_KEYS[0] if GROQ_API_KEYS else ""

# Current Groq production routing as of September 2026.
# Primary: GPT-OSS 120B for stronger reasoning + JSON Schema Mode.
# Secondary: GPT-OSS 20B for fast verification/fallback.
GROQ_MODEL: str = _clean_env_val("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_MODEL_CANDIDATES: List[str] = []
for _model_name in (GROQ_MODEL, "openai/gpt-oss-120b", "openai/gpt-oss-20b"):
    if _model_name and _model_name not in GROQ_MODEL_CANDIDATES:
        GROQ_MODEL_CANDIDATES.append(_model_name)
del _model_name
GROQ_REASONING_EFFORT: str = _clean_env_val("GROQ_REASONING_EFFORT", "high").lower()
if GROQ_REASONING_EFFORT not in {"low", "medium", "high"}:
    GROQ_REASONING_EFFORT = "high"
GROQ_TIMEOUT_SECONDS: float = _env_float("GROQ_TIMEOUT_SECONDS", 30.0, 5.0, 90.0)

AI_VERIFIER_ENABLED: bool = _env_bool("AI_VERIFIER_ENABLED", True)
AI_EVIDENCE_HINTS_ENABLED: bool = _env_bool("AI_EVIDENCE_HINTS_ENABLED", True)
AI_VERIFIER_MODEL: str = _clean_env_val("AI_VERIFIER_MODEL", "openai/gpt-oss-20b")
AI_VERIFIER_REASONING_EFFORT: str = _clean_env_val("AI_VERIFIER_REASONING_EFFORT", "high").lower()
AI_VERIFIER_MAX_COMPLETION_TOKENS: int = _env_int("AI_VERIFIER_MAX_COMPLETION_TOKENS", 3000, 800, 6000)
if AI_VERIFIER_REASONING_EFFORT not in {"low", "medium", "high"}:
    AI_VERIFIER_REASONING_EFFORT = "medium"
AI_LOW_CONFIDENCE_REVIEW_THRESHOLD: float = _env_float("AI_LOW_CONFIDENCE_REVIEW_THRESHOLD", 0.72, 0.0, 1.0)
AI_MAX_BODY_CHARS: int = _env_int("AI_MAX_BODY_CHARS", 18000, 4000, 40000)
AI_MAX_ATTACHMENT_CHARS: int = _env_int("AI_MAX_ATTACHMENT_CHARS", 12000, 2000, 30000)
AI_MAX_COMPLETION_TOKENS: int = _env_int("AI_MAX_COMPLETION_TOKENS", 5000, 1200, 8000)
MAX_ATTACHMENT_SIZE_BYTES: int = _env_int("MAX_ATTACHMENT_SIZE_BYTES", 25 * 1024 * 1024, 1 * 1024 * 1024, 50 * 1024 * 1024)
MAX_TOTAL_ATTACHMENT_BYTES: int = _env_int("MAX_TOTAL_ATTACHMENT_BYTES", 40 * 1024 * 1024, 2 * 1024 * 1024, 100 * 1024 * 1024)
REQUIRE_DOCUMENT_EVIDENCE: bool = _env_bool("REQUIRE_DOCUMENT_EVIDENCE", False)

USER_TIMEZONE: str = _clean_env_val("USER_TIMEZONE", "Asia/Kolkata")
DEFAULT_EVENT_TIME: str = _clean_env_val("DEFAULT_EVENT_TIME", "09:00")

GOOGLE_CLIENT_ID: str = (
    _clean_env_val("GOOGLE_CLIENT_ID")
    or _clean_env_val("CLIENT_ID")
    or _clean_env_val("GOOGLE_OAUTH_CLIENT_ID")
)
GOOGLE_CLIENT_SECRET: str = (
    _clean_env_val("GOOGLE_CLIENT_SECRET")
    or _clean_env_val("CLIENT_SECRET")
    or _clean_env_val("GOOGLE_OAUTH_CLIENT_SECRET")
)

# Optional at-rest credential encryption. When supplied, future token blobs are encrypted.
TOKEN_ENCRYPTION_KEY: str = _clean_env_val("TOKEN_ENCRYPTION_KEY")
TOKEN_ENCRYPTION_REQUIRED: bool = _env_bool("TOKEN_ENCRYPTION_REQUIRED", BACKEND_URL.lower().startswith("https://"))
SESSION_TTL_SECONDS: int = _env_int("SESSION_TTL_SECONDS", 7 * 24 * 3600, 900, 30 * 24 * 3600)
SESSION_SECURE_COOKIE: bool = _env_bool("SESSION_SECURE_COOKIE", BACKEND_URL.lower().startswith("https://"))

LOG_FILE: Path = BASE_DIR / "calendar_assistant.log"
DB_PATH: Path = BASE_DIR / "assistant_v2.db"


class AppConfig:
    poll_interval: int = _env_int("POLL_INTERVAL", 15, 5, 300)
    confidence_threshold: float = _env_float("CONFIDENCE_THRESHOLD", 0.70, 0.0, 1.0)
