"""Report assembly. The LLM only writes the summary; findings come straight from the database."""

from typing import Any

from operyxa.db.models import Finding, RunStep

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def finding_view(f: Finding) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "title": f.title,
        "severity": f.severity,
        "category": f.category,
        "description": f.description,
        "recommendation": f.recommendation,
        "agent": f.agent,
        "step": f.step_key,
        "status": f.status,
        "evidence": f.evidence,
    }


def build_body(
    *,
    objective: str,
    repository: str,
    commit_sha: str,
    summary: str,
    recommendations: list[str],
    steps: list[RunStep],
    findings: list[Finding],
    stop_reason: str | None,
    counters: dict[str, Any],
) -> dict[str, Any]:
    ordered = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    return {
        "objective": objective,
        "repository": repository,
        "commit_sha": commit_sha,
        "summary": summary,
        "recommendations": recommendations,
        "stop_reason": stop_reason,
        "steps": [
            {
                "id": s.step_key,
                "agent": s.agent,
                "action": s.action,
                "status": s.status,
                "summary": s.summary,
                "error": s.error,
            }
            for s in steps
        ],
        "findings": [finding_view(f) for f in ordered if f.status == "verified"],
        "unverified_findings": [finding_view(f) for f in ordered if f.status != "verified"],
        "usage": counters,
        "disclaimer": "Automated review. It does not certify the security of the software "
        "nor guarantee the absence of vulnerabilities.",
    }


def render_markdown(body: dict[str, Any]) -> str:
    lines = [
        f"# Review report — {body['repository']}",
        "",
        f"- **Objective:** {body['objective']}",
        f"- **Commit:** `{body['commit_sha']}`",
    ]
    if body["stop_reason"]:
        lines.append(f"- **Partial report:** the run stopped early (`{body['stop_reason']}`)")
    lines += ["", "## Summary", "", body["summary"], ""]
    if body["recommendations"]:
        lines += (
            ["## Recommendations", ""]
            + [f"{i}. {r}" for i, r in enumerate(body["recommendations"], 1)]
            + [""]
        )
    lines += ["## Verified findings", ""]
    if not body["findings"]:
        lines += ["No verified finding.", ""]
    for f in body["findings"]:
        lines += [f"### [{f['severity'].upper()}] {f['title']}", "", f["description"], ""]
        if f["recommendation"]:
            lines += [f"**Recommendation:** {f['recommendation']}", ""]
        for e in f["evidence"]:
            where = e.get("path") or e.get("tool")
            if e.get("start_line"):
                where += f":{e['start_line']}"
            quote = " ".join(str(e["quote"]).split())[:200].replace("`", "'")
            lines.append(f"- Evidence `{e['evidence_id']}` — {where} — `{quote}`")
        lines.append("")
    if body["unverified_findings"]:
        lines += ["## Unverified claims (excluded from conclusions)", ""]
        lines += [
            f"- {f['title']} — evidence could not be confirmed" for f in body["unverified_findings"]
        ] + [""]
    lines += ["## Steps", ""] + [
        f"- `{s['id']}` ({s['agent']}): **{s['status']}**"
        + (f" — {s['error']}" if s["error"] else "")
        for s in body["steps"]
    ]
    lines += ["", f"_{body['disclaimer']}_", ""]
    return "\n".join(lines)
