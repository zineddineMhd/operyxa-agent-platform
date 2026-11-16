"""Deterministic merge of findings that describe the same thing.

Two agents often report one problem twice (a committed .env file, an unsafe workflow trigger).
Findings are duplicates when their primary evidence is the same place: the same file and line,
or — for evidence without a line — the same file and the same category. The most severe one is kept and
inherits the other's distinct evidence; the others are marked `duplicate`, never deleted.
"""

from typing import Any

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
MAX_EVIDENCE = 5


def _keys(finding: Any) -> set[tuple]:
    """Identity of a finding: where its *first* verified evidence points.

    Only the primary evidence counts. Supporting evidence often touches a neighbouring line
    ("runs as root" also quoting the FROM line) and would merge two different problems.
    """
    for item in finding.evidence:
        if not item.get("verified") or not item.get("path"):
            continue
        if item.get("start_line"):
            return {(item["path"], item["start_line"])}
        return {(item["path"], "file", finding.category)}
    return set()


def find_duplicates(findings: list[Any]) -> list[tuple[Any, list[Any]]]:
    """Group verified findings; return (kept, duplicates) for every group of two or more."""
    verified = [f for f in findings if f.status == "verified"]
    parent = list(range(len(verified)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[tuple, int] = {}
    for index, finding in enumerate(verified):
        for key in _keys(finding):
            if key in seen:
                parent[root(index)] = root(seen[key])
            else:
                seen[key] = index
    groups: dict[int, list[Any]] = {}
    for index, finding in enumerate(verified):
        groups.setdefault(root(index), []).append(finding)
    merged = []
    for group in groups.values():
        if len(group) > 1:
            group.sort(
                key=lambda f: SEVERITY_RANK.get(f.severity, 9)
            )  # stable: first reported wins ties
            merged.append((group[0], group[1:]))
    return merged


def merged_evidence(kept: Any, duplicates: list[Any]) -> list[dict[str, Any]]:
    evidence = list(kept.evidence)
    known = {(e.get("evidence_id"), e.get("quote")) for e in evidence}
    for duplicate in duplicates:
        for item in duplicate.evidence:
            key = (item.get("evidence_id"), item.get("quote"))
            if item.get("verified") and key not in known and len(evidence) < MAX_EVIDENCE:
                evidence.append(item)
                known.add(key)
    return evidence
