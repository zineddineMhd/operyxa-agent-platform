"""Deterministic evidence verification.

A finding is only "verified" if each quote it cites really appears in the output of a tool call
made during this run. File paths and line numbers are derived here from the stored output —
they are never taken from the model.
"""

import json
import re
from typing import Any

from operyxa.db.models import ToolCall

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _locate(node: Any, quote: str, inherited_path: str | None = None) -> dict[str, Any] | None:
    """Find the string leaf containing `quote` and the path/line of its enclosing object."""
    if isinstance(node, dict):
        path = node.get("path") if isinstance(node.get("path"), str) else inherited_path
        for key, value in node.items():
            if isinstance(value, str) and key != "path":
                line = _line_in(value, quote)
                if line is not None:
                    base = node.get("start_line") if key == "content" else node.get("line")
                    start = base + line - 1 if isinstance(base, int) else None
                    end = start + quote.strip().count("\n") if start is not None else None
                    return {"path": path, "start_line": start, "end_line": end}
        for value in node.values():
            if isinstance(value, (dict, list)):
                found = _locate(value, quote, path)
                if found:
                    return found
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


def verify_reference(ref: dict[str, Any], calls: dict[str, ToolCall]) -> dict[str, Any]:
    evidence_id, quote = str(ref.get("evidence_id", "")), str(ref.get("quote", ""))
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
    try:
        location = _locate(json.loads(text), quote)
    except ValueError:
        location = None
    if location is None:
        if _norm(quote) not in _norm(text):
            return {**out, "reason": "quote not found in tool output"}
        location = {"path": call.args.get("path"), "start_line": None, "end_line": None}
    if location.get("path") is None:
        location["path"] = call.args.get("path")
    return {**out, **location, "verified": True}


def verify_finding(
    evidence: list[dict[str, Any]], calls: dict[str, ToolCall]
) -> tuple[str, list[dict]]:
    checked = [verify_reference(ref, calls) for ref in evidence]
    status = "verified" if checked and all(e["verified"] for e in checked) else "unverified"
    return status, checked
