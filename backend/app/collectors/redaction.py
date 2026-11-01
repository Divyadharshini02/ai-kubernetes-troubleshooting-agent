"""Best-effort removal of secrets from free text such as logs and event messages.

Pattern matching cannot catch every secret, so collectors also avoid reading
sensitive fields at all. Over-redacting a harmless value is the accepted trade-off.
"""

import re

MASK = "[REDACTED]"

_SENSITIVE_KEY = (
    r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|credential)[\w.-]*"
)

_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S), MASK),
    (re.compile(r"(://[^/\s:@]+:)[^/\s@]+(@)"), rf"\g<1>{MASK}\g<2>"),
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?:(?:bearer|basic)\s+)?[^\s\"',;]+"), rf"\g<1>{MASK}"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+"), rf"\g<1>{MASK}"),
    (re.compile(rf"(?i)\b({_SENSITIVE_KEY})([\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]+"), rf"\g<1>\g<2>{MASK}"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9-]{20,}\b"), MASK),
]


class Redactor:
    """Redacts text and counts how many replacements were made."""

    def __init__(self) -> None:
        self.count = 0

    def text(self, value: str | None) -> str | None:
        if value is None:
            return None
        for pattern, replacement in _PATTERNS:
            value, replaced = pattern.subn(replacement, value)
            self.count += replaced
        return value
