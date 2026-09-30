"""Strip credentials from text before it is logged or returned."""

import re

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")


def redact(text: str, limit: int = 500) -> str:
    cleaned = _BEARER.sub("Bearer [redacted]", text)
    cleaned = _JWT.sub("[redacted-jwt]", cleaned)
    if len(cleaned) > limit:
        return cleaned[:limit] + "..."
    return cleaned
