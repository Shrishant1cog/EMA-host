import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOG_FILE = BASE_DIR / "calendar_assistant.log"

SUCCESS_LEVEL_NUM = 25
FILTER_LEVEL_NUM = 22
logging.addLevelName(SUCCESS_LEVEL_NUM, "SUCCESS")
logging.addLevelName(FILTER_LEVEL_NUM, "FILTER")


def success(self, message, *args, **kwargs):
    if self.isEnabledFor(SUCCESS_LEVEL_NUM):
        self._log(SUCCESS_LEVEL_NUM, message, args, **kwargs)


def filter_event(self, message, *args, **kwargs):
    if self.isEnabledFor(FILTER_LEVEL_NUM):
        self._log(FILTER_LEVEL_NUM, message, args, **kwargs)


logging.Logger.success = success
logging.Logger.filter_event = filter_event


def humanize_error(error: Exception) -> str:
    if not error:
        return "Unknown error occurred."
    err_str = str(error)
    err_type = type(error).__name__
    if "11001" in err_str or "getaddrinfo failed" in err_str or "NameResolutionError" in err_str:
        return "Internet/DNS lookup failed: Could not resolve server address. Check your internet connection."
    if "Connection refused" in err_str or "ConnectionRefusedError" in err_type:
        return "Connection failed: The local server is unreachable or port is blocked."
    if "timed out" in err_str.lower() or "TimeoutError" in err_type:
        return "Connection timed out: The remote service took too long to respond."
    if "invalid_grant" in err_str or "Token has been expired or revoked" in err_str:
        return "Google authorization expired: Please reconnect your Google account."
    if "insufficientPermissions" in err_str or "403" in err_str:
        return "Permission denied: Google rejected the action due to insufficient OAuth permissions."
    if "429" in err_str or "rate_limit_exceeded" in err_str:
        return "AI provider rate limit reached: EMA will retry/rotate according to configuration."
    if "invalid_api_key" in err_str:
        return "AI API key is invalid: Verify the configured AI key."
    if "JSONDecodeError" in err_str:
        return "Data format error: AI response could not be parsed as valid JSON event data."
    return f"{err_type}: {err_str}"


class CleanTerminalFormatter(logging.Formatter):
    def format(self, record):
        timestamp = self.formatTime(record, "%H:%M:%S")
        level = record.levelname.upper()
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1]:
            english_err = humanize_error(record.exc_info[1])
            msg = f"{msg} | Reason: {english_err}" if msg else english_err
        return f"[{timestamp}] [{level}] {msg}"


_CONFIGURED = False
_SHARED_FILE_HANDLER: Optional[logging.Handler] = None
_SHARED_CONSOLE_HANDLER: Optional[logging.Handler] = None


def _ensure_shared_handlers(level: int):
    global _CONFIGURED, _SHARED_FILE_HANDLER, _SHARED_CONSOLE_HANDLER
    if _CONFIGURED:
        return
    formatter = CleanTerminalFormatter()

    _SHARED_CONSOLE_HANDLER = logging.StreamHandler(sys.stdout)
    _SHARED_CONSOLE_HANDLER.setLevel(level)
    _SHARED_CONSOLE_HANDLER.setFormatter(formatter)

    _SHARED_FILE_HANDLER = RotatingFileHandler(
        LOG_FILE,
        maxBytes=3 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    _SHARED_FILE_HANDLER.setLevel(level)
    _SHARED_FILE_HANDLER.setFormatter(formatter)

    _CONFIGURED = True


def setup_logger(name: str = "AssistantEngine", level: str = "INFO") -> logging.Logger:
    log_level = getattr(logging, level.upper(), logging.INFO)
    _ensure_shared_handlers(log_level)
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.propagate = False

    # Named service loggers share the same handlers, avoiding duplicate files and
    # preserving central visibility for the admin diagnostics system.
    if _SHARED_CONSOLE_HANDLER not in logger.handlers:
        logger.addHandler(_SHARED_CONSOLE_HANDLER)
    if _SHARED_FILE_HANDLER not in logger.handlers:
        logger.addHandler(_SHARED_FILE_HANDLER)
    return logger


get_logger = setup_logger
