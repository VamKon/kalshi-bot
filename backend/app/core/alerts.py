"""
In-memory alert store — captures WARNING and ERROR log records and exposes
them to the Streamlit UI so the user doesn't have to tail kubectl logs.

Usage:
    from app.core.alerts import alert_store, install_log_handler
    install_log_handler()   # call once at startup

    # Manual alerts from anywhere in the codebase:
    alert_store.error("Trade execution failed", detail="order rejected by Kalshi")
    alert_store.warning("Odds API quota low", detail="450/500 requests used")
"""
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional


MAX_ALERTS = 50          # keep last 50 alerts
ALERT_LEVELS = {"error", "warning", "info"}


class Alert:
    __slots__ = ("level", "message", "detail", "source", "ts")

    def __init__(self, level: str, message: str,
                 detail: Optional[str] = None, source: Optional[str] = None):
        self.level   = level        # "error" | "warning" | "info"
        self.message = message
        self.detail  = detail
        self.source  = source       # logger name / module
        self.ts      = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "level":   self.level,
            "message": self.message,
            "detail":  self.detail,
            "source":  self.source,
            "ts":      self.ts.isoformat(),
        }


class AlertStore:
    """Thread-safe ring-buffer of recent alerts."""

    def __init__(self, maxlen: int = MAX_ALERTS):
        self._alerts: deque[Alert] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def _add(self, level: str, message: str,
             detail: Optional[str] = None, source: Optional[str] = None) -> None:
        with self._lock:
            self._alerts.append(Alert(level, message, detail, source))

    def error(self, message: str, detail: Optional[str] = None,
              source: Optional[str] = None) -> None:
        self._add("error", message, detail, source)

    def warning(self, message: str, detail: Optional[str] = None,
                source: Optional[str] = None) -> None:
        self._add("warning", message, detail, source)

    def info(self, message: str, detail: Optional[str] = None,
             source: Optional[str] = None) -> None:
        self._add("info", message, detail, source)

    def clear(self) -> None:
        with self._lock:
            self._alerts.clear()

    def get_all(self) -> list[dict]:
        with self._lock:
            return [a.to_dict() for a in reversed(self._alerts)]  # newest first

    def get_unresolved(self) -> list[dict]:
        """Errors and warnings only — what the user needs to act on."""
        with self._lock:
            return [
                a.to_dict() for a in reversed(self._alerts)
                if a.level in ("error", "warning")
            ]

    @property
    def error_count(self) -> int:
        with self._lock:
            return sum(1 for a in self._alerts if a.level == "error")

    @property
    def warning_count(self) -> int:
        with self._lock:
            return sum(1 for a in self._alerts if a.level == "warning")


# Singleton used across the whole app
alert_store = AlertStore()


# ── Logging integration ────────────────────────────────────────────────────────

# Modules that generate high-volume routine logs — suppress from alerts
_NOISY_LOGGERS = {
    "apscheduler",
    "httpx",
    "httpcore",
    "uvicorn.access",
    "sqlalchemy.engine",
}

# Substrings that indicate routine non-actionable log lines
_NOISE_PHRASES = (
    "pre-filter skip",
    "Skipping",
    "cache hit",
    "Balance in sync",
    "OddsService match",
    "News cache",
    "Rate-limited",   # handled with retry — not an alert
)


class _AlertLogHandler(logging.Handler):
    """
    Logging handler that funnels WARNING+ records into the alert_store.
    Filters out noisy routine messages so only actionable issues surface.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Ignore noisy loggers entirely
        if any(record.name.startswith(n) for n in _NOISY_LOGGERS):
            return

        msg = record.getMessage()

        # Ignore routine informational noise
        if any(phrase in msg for phrase in _NOISE_PHRASES):
            return

        level = "error" if record.levelno >= logging.ERROR else "warning"
        # Truncate very long messages; include exception info if present
        detail = None
        if record.exc_info:
            import traceback
            detail = "".join(traceback.format_exception(*record.exc_info)).strip()[-400:]

        alert_store._add(
            level   = level,
            message = msg[:200],
            detail  = detail,
            source  = record.name,
        )


_handler_installed = False


def install_log_handler() -> None:
    """
    Attach the alert handler to the root logger.
    Safe to call multiple times — only installs once.
    """
    global _handler_installed
    if _handler_installed:
        return
    handler = _AlertLogHandler()
    handler.setLevel(logging.WARNING)
    logging.getLogger().addHandler(handler)
    _handler_installed = True
