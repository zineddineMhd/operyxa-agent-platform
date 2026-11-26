"""Deterministic evidence verification.

A finding is only "verified" if each quote it cites really appears in the output of a tool call
made during this run. File paths and line numbers are derived here from the stored output —
they are never taken from the model.
"""

import json
import re
from typing import Any

from operyxa.db.models import ToolCall
from operyxa.gateway.redaction import MARKER, redact

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _locate(node: Any, quote: str, inherited_path: str | None = None) -> dict[str, Any] | None:
    """Find the string leaf containing `quote` and the path/line of its enclosing object."""
    if isinstance(node, dict):
        path = node.get("path") if isinstance(node.get("path"), str) else inherited_path
        direct = None
        for key, value in node.items():
            if isinstance(value, str) and key != "path":
                line = _line_in(value, quote)
                if line is not None:
                    base = node.get("start_line") if key == "content" else node.get("line")
                    start = base + line - 1 if isinstance(base, int) else None
                    end = start + quote.strip().count("\n") if start is not None else None
                    direct = {"path": path, "start_line": start, "end_line": end}
                    break
        if direct is not None and direct["path"] is not None:
            return direct
        # A match without a file (e.g. a raw log) is a fallback: a nested object holding the same
        # text with its own path (e.g. a failing test entry) locates the claim better.
        for value in node.values():
            if isinstance(value, (dict, list)):
                found = _locate(value, quote, path)
                if found and (found["path"] is not None or direct is None):
                    return found
        if direct is not None:
            return direct
        if isinstance(node.get("path"), str) and _norm(quote) == node["path"]:
            return {"path": path, "start_line": None, "end_line": None}
    elif isinstance(node, list):
        for value in node:
            found = _locate(value, quote, inherited_path)
            if found:
                return found
    return None


def _line_in(text: str, quote: str) -> int | None:
    """1-based line of `quote` inside `text`, tolerant to whitespace differences."""
    quote = quote.strip()
    index = text.find(quote)
    if index >= 0:
        return text.count("\n", 0, index) + 1
    if _norm(quote) not in _norm(text):
        return None
    first = _norm(quote.splitlines()[0])
    for number, line in enumerate(text.splitlines(), start=1):
        if first and first in _norm(line):
            return number
    return 1


def _canon(text: str) -> str:
    """All redaction markers look alike: the model cannot know which detector fired."""
    return MARKER.sub("[REDACTED]", text)


def _canon_tree(node: Any) -> Any:
    if isinstance(node, str):
        return _canon(node)
    if isinstance(node, list):
        return [_canon_tree(v) for v in node]
    if isinstance(node, dict):
        return {k: _canon_tree(v) for k, v in node.items()}
    return node


def _find(text: str, quote: str) -> dict[str, Any] | None:
    try:
        location = _locate(json.loads(text), quote)
    except ValueError:
        location = None
    if location is None and _norm(quote) in _norm(text):
        location = {"path": None, "start_line": None, "end_line": None}
    return location


def verify_reference(ref: dict[str, Any], calls: dict[str, ToolCall]) -> dict[str, Any]:
    evidence_id = str(ref.get("evidence_id", ""))
    # The stored quote never carries a secret, even one the model guessed or recalled.
    raw_quote = str(ref.get("quote", ""))
    quote = redact(raw_quote)
    out: dict[str, Any] = {
        "evidence_id": evidence_id,
        "quote": quote,
        "verified": False,
        "tool": None,
        "path": None,
        "start_line": None,
        "end_line": None,
    }
    call = calls.get(evidence_id)
    if call is None:
        return {**out, "reason": "unknown evidence id"}
    out["tool"] = call.tool
    text = call.result_text or ""
    if len(_norm(quote)) < 3:
        return {**out, "reason": "quote too short"}
    location = _find(text, quote)
    if location is None and MARKER.search(text):
        # The model quoted a masked line with its (guessed) original value: compare both sides
        # with the secrets masked, so the claim is checked against what the model really saw.
        canon_quote = _canon(quote)
        try:
            canon_text = json.dumps(_canon_tree(json.loads(text)), ensure_ascii=False)
        except ValueError:
            canon_text = _canon(text)
        location = _find(canon_text, canon_quote)
        if location is not None:
            out["quote"], out["quote_redacted"] = canon_quote, True
    if location is None:
        return {**out, "reason": "quote not found in tool output"}
    if location.get("path") is None:
        location["path"] = call.args.get("path")
    return {**out, **location, "verified": True}


def verify_finding(
    evidence: list[dict[str, Any]], calls: dict[str, ToolCall]
) -> tuple[str, list[dict]]:
    checked = [verify_reference(ref, calls) for ref in evidence]
    status = "verified" if checked and all(e["verified"] for e in checked) else "unverified"
    return status, checked
