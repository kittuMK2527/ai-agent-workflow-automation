"""Logging setup with secret redaction."""

from __future__ import annotations

import logging
import os
import re
import traceback

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|token|secret)(\"?\s*[:=]\s*\"?)([^\s\",;]{6,})"),
]
_EMAIL = re.compile(r"([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*@([A-Za-z0-9\-]+\.[A-Za-z.]{2,})")


def redact(text: object) -> str:
    """Remove API keys / bearer tokens (and the configured key value) from text."""
    s = str(text)
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if len(key) >= 8:
        s = s.replace(key, "[REDACTED]")
    s = _PATTERNS[0].sub("[REDACTED]", s)
    s = _PATTERNS[1].sub("Bearer [REDACTED]", s)
    s = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", s)
    return s


def mask_email(text: object) -> str:
    """``alex.morgan@example.com`` -> ``a***@example.com`` (used in the audit log)."""
    return _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", str(text))


def log_exception(logger: logging.Logger, message: str, *args: object) -> None:
    """Log an error plus a *redacted* traceback.

    ``logger.exception`` would write the raw traceback, whose exception messages can
    contain secrets, straight past any message filter. This never passes ``exc_info``.
    """
    logger.error("%s\n%s", message % args if args else message, redact(traceback.format_exc()))


class RedactingFormatter(logging.Formatter):
    """Defence in depth: redact the fully formatted record, traceback included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        root.setLevel(level)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level)
