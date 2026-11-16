"""Secret redaction applied to every tool output *before* it is stored or sent to the LLM."""

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
        ),
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    ),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("url_credentials", re.compile(r"(?<=://)[^\s:/@]+:[^\s:/@]+(?=@)")),
]
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[\w.-]*(?:secret|token|passwd|password|api[_-]?key|private[_-]?key)[\w.-]*
      ["']?\s*[:=]\s*["']?)
    ([^\s"'<>{}$]{8,})
    """
)


def redact(text: str) -> str:
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    return _ASSIGNMENT.sub(lambda m: f"{m.group(1)}[REDACTED:secret]", text)
