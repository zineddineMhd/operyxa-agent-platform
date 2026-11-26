"""Size cap for tool outputs that keeps them valid JSON.

Cutting the serialised text at N characters breaks the JSON, and the evidence verifier then
loses the `path` / `start_line` context it needs to locate a quote. Instead the largest
strings are shortened on line boundaries and the longest lists lose their tail, until the
output fits. A shortened file window stays a prefix of the file, so its line numbers stay right.
"""

import json
from typing import Any

NOTE = "[output truncated by gateway]"


def _size(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False))


def _largest(node: Any, parent: Any = None, key: Any = None, best=None):
    """(size, parent, key, kind) of the largest string or list in the tree."""
    if isinstance(node, str):
        if best is None or len(node) > best[0]:
            best = (len(node), parent, key, "str")
    elif isinstance(node, list):
        size = _size(node)
        if len(node) > 1 and (best is None or size > best[0]):
            best = (size, parent, key, "list")
        for i, value in enumerate(node):
            best = _largest(value, node, i, best)
    elif isinstance(node, dict):
        for k, value in node.items():
            best = _largest(value, node, k, best)
    return best


def _shorten(text: str, target: int) -> str:
    """Keep whole lines while possible; never cut inside the first `target` characters."""
    if len(text) <= target:
        return text
    cut = text.rfind("\n", 0, target)
    return text[: cut if cut > 0 else target]


def truncate_output(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, (dict, list)):
        return text[:limit] + "\n" + NOTE, True
    for _ in range(200):
        excess = _size(data) - limit
        if excess <= 0:
            break
        found = _largest(data)
        if found is None:
            break
        size, parent, key, kind = found
        if parent is None:  # the root itself is a list
            if kind == "list" and len(data) > 1:
                data = data[: max(1, len(data) // 2)]
                continue
            break
        value = parent[key]
        if kind == "str":
            parent[key] = _shorten(value, max(0, len(value) - excess - 64))
        else:
            keep = max(1, int(len(value) * max(0.1, 1 - (excess + 64) / max(size, 1))))
            parent[key] = value[: min(keep, len(value) - 1)]
        if isinstance(data, dict):
            data["gateway_truncated"] = True
    out = json.dumps(data, ensure_ascii=False)
    if len(out) > limit:  # pathological shape: fall back to a raw cut
        return out[:limit] + "\n" + NOTE, True
    return out, True
