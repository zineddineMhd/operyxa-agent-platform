"""Secret redaction applied to every tool output *before* it is stored or sent to the LLM.

Two properties matter as much as the masking itself:

- Redaction runs on the decoded text. Tool outputs are JSON, where a quoted value such as
  `KEY = "value"` is serialised as `KEY = \\"value\\"`: patterns applied to the raw JSON miss
  it. `redact_tool_output` therefore walks the JSON and redacts every string leaf.
- Line numbers survive. A masked multi-line block keeps its line count, so the evidence
  verifier still derives the right line for anything that follows it.
"""

import json
import re
from typing import Any

_PRIVATE_KEY = re.compile(
    r"(-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----)(.*?)"
    r"(-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.S,
)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    ),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("nvidia_key", re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}\b")),
    ("stripe_key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("url_credentials", re.compile(r"(?<=://)[^\s:/@]+:[^\s:/@]+(?=@)")),
]
_KEY_NAME = r"[\w.-]*(?:secret|token|passwd|password|pwd|api[_-]?key|private[_-]?key|access[_-]?key|credential)[\w.-]*"
# `password = "two words"`, `"api_key": "x"`, `token: 'abc'`: the whole quoted value.
_QUOTED_ASSIGNMENT = re.compile(
    rf"""(?ix)
    (\b{_KEY_NAME}["']?\s*(?::|=|:=|=>)\s*)
    (["'])((?:(?!\2)[^\n\\]|\\.){{4,}})\2
    """
)
# `PASSWORD=value`: an unquoted value up to whitespace.
_BARE_ASSIGNMENT = re.compile(
    rf"""(?ix)
    (\b{_KEY_NAME}["']?\s*(?::|=|:=|=>)\s*)
    ([^\s"'<>{{}}$\[\],;]{{8,}})
    """
)
_PLACEHOLDER = re.compile(
    r"^(?:\[REDACTED:\w+\]|\$\{?\w+\}?|<[^>]+>|x+|\*+|changeme|example)$", re.I
)
MARKER = re.compile(r"\[REDACTED:\w+\]")


def _mask_private_key(match: re.Match[str]) -> str:
    # Keep BEGIN/END and the line count: the verifier computes line numbers from this text.
    body = match.group(2)
    lines = body.count("\n")
    masked = "[REDACTED:private_key]" + "\n" * lines if lines else "[REDACTED:private_key]"
    return f"{match.group(1)}{masked}{match.group(3)}"


def _mask_quoted(match: re.Match[str]) -> str:
    value = match.group(3)
    if _PLACEHOLDER.match(value):
        return match.group(0)
    quote = match.group(2)
    return f"{match.group(1)}{quote}[REDACTED:secret]{quote}"


def _mask_bare(match: re.Match[str]) -> str:
    if _PLACEHOLDER.match(match.group(2)):
        return match.group(0)
    return f"{match.group(1)}[REDACTED:secret]"


def redact(text: str) -> str:
    text = _PRIVATE_KEY.sub(_mask_private_key, text)
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    text = _QUOTED_ASSIGNMENT.sub(_mask_quoted, text)
    return _BARE_ASSIGNMENT.sub(_mask_bare, text)


def redact_value(node: Any) -> Any:
    """Redact every string of a decoded JSON value (keys included)."""
    if isinstance(node, str):
        return redact(node)
    if isinstance(node, list):
        return [redact_value(v) for v in node]
    if isinstance(node, dict):
        return {redact(k) if isinstance(k, str) else k: redact_value(v) for k, v in node.items()}
    return node


def redact_tool_output(text: str) -> str:
    """Redact a tool output. JSON is redacted leaf by leaf on its decoded strings."""
    try:
        data = json.loads(text)
    except ValueError:
        return redact(text)
    if not isinstance(data, (dict, list)):
        return redact(text)
    return json.dumps(redact_value(data), ensure_ascii=False)
