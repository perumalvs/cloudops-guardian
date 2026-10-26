"""Sensitive value redaction helpers."""
import re

_SENSITIVE_KEY = re.compile(r"token|secret|password|passwd|authorization|credential|api[_-]?key|session", re.I)
_APPROVE = re.compile(r"(APPROVE\s+PLAN-[A-Za-z0-9]+)\s+\S+", re.I)
_AWS_KEY = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")
MAX_STR = 2000
REDACTED = "[REDACTED]"


def redact_text(text):
    text = _APPROVE.sub(r"\1 " + REDACTED, text)
    text = _AWS_KEY.sub(REDACTED, text)
    return text if len(text) <= MAX_STR else text[:MAX_STR] + "...[truncated]"


def redact(obj):
    """Return a copy of obj that is safe to audit/log."""
    if isinstance(obj, dict):
        return {k: (REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
