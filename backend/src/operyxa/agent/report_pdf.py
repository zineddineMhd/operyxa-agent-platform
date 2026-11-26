"""PDF export of a report (F-11). Rendered from the stored report body, never by the model.

Core PDF fonts only cover Latin-1: other characters are transliterated or replaced, so the
export never fails on repository content.
"""

from datetime import datetime
from typing import Any

from fpdf import FPDF

_TRANSLATE = str.maketrans(
    {
        "—": "-",
        "–": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "•": "-",
        " ": " ",
        "→": "->",
        "≤": "<=",
        "≥": ">=",
        "✓": "v",
        "✗": "x",
    }
)
SEVERITY_COLOURS = {
    "critical": (153, 27, 27),
    "high": (194, 65, 12),
    "medium": (161, 98, 7),
    "low": (21, 94, 117),
    "info": (71, 85, 105),
}


def _t(value: Any) -> str:
    text = str(value if value is not None else "").translate(_TRANSLATE)
    return text.encode("latin-1", "replace").decode("latin-1")


class _Doc(FPDF):
    def __init__(self, title: str):
        super().__init__(format="A4")
        self._title = title
        self.set_margins(18, 18, 18)
        self.set_auto_page_break(True, 18)
        self.set_title(title)
        self.set_creator("Operyxa")

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 6, _t(self._title), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 6, f"Operyxa - page {self.page_no()}/{{nb}}", align="C")

    def heading(self, text: str, size: int = 13) -> None:
        self.ln(3)
        self.set_font("Helvetica", "B", size)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 7, _t(text), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def para(self, text: str, size: int = 10, style: str = "", colour=(40, 40, 40)) -> None:
        self.set_font("Helvetica", style, size)
        self.set_text_color(*colour)
        self.multi_cell(0, 5, _t(text), new_x="LMARGIN", new_y="NEXT")

    def mono(self, text: str) -> None:
        self.set_font("Courier", "", 8)
        self.set_text_color(60, 60, 60)
        self.multi_cell(0, 4, _t(text), new_x="LMARGIN", new_y="NEXT")


def render_pdf(body: dict[str, Any], *, partial: bool, created_at: datetime | None) -> bytes:
    pdf = _Doc(f"Review report - {body.get('repository', '')}")
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 20, 20)
    pdf.multi_cell(
        0, 9, _t(f"Review report - {body.get('repository', '')}"), new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(2)
    meta = [
        ("Objective", body.get("objective")),
        ("Commit", body.get("commit_sha")),
        ("Generated", created_at.strftime("%Y-%m-%d %H:%M UTC") if created_at else None),
    ]
    if partial or body.get("stop_reason"):
        meta.append(("Partial report", f"the run stopped early ({body.get('stop_reason')})"))
    usage = body.get("usage") or {}
    if usage:
        meta.append(
            (
                "Usage",
                f"{usage.get('llm_calls', 0)} model calls, {usage.get('tool_calls', 0)} tool "
                f"calls, about ${float(usage.get('cost_usd', 0)):.4f}",
            )
        )
    for label, value in meta:
        if value:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(90, 90, 90)
            pdf.cell(32, 5, _t(label))
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(30, 30, 30)
            pdf.multi_cell(0, 5, _t(value), new_x="LMARGIN", new_y="NEXT")

    pdf.heading("Summary")
    pdf.para(body.get("summary", ""))
    if body.get("recommendations"):
        pdf.heading("Recommendations")
        for i, rec in enumerate(body["recommendations"], 1):
            pdf.para(f"{i}. {rec}")

    findings = body.get("findings") or []
    pdf.heading(f"Verified findings ({len(findings)})")
    if not findings:
        pdf.para("No verified finding.", style="I")
    for f in findings:
        colour = SEVERITY_COLOURS.get(f.get("severity", ""), (40, 40, 40))
        pdf.ln(1)
        pdf.para(f"[{str(f.get('severity', '')).upper()}] {f.get('title', '')}", 11, "B", colour)
        pdf.para(
            f"Category: {f.get('category', '')} - agent: {f.get('agent', '')}",
            8,
            "",
            (110, 110, 110),
        )
        pdf.para(f.get("description", ""))
        if f.get("recommendation"):
            pdf.para(f"Recommendation: {f['recommendation']}", 9, "I")
        for e in f.get("evidence", []):
            where = e.get("path") or e.get("tool") or ""
            if e.get("start_line"):
                where += f":{e['start_line']}"
            quote = " ".join(str(e.get("quote", "")).split())[:300]
            pdf.mono(f"{e.get('evidence_id')}  {where}\n  {quote}")
    unverified = body.get("unverified_findings") or []
    if unverified:
        pdf.heading("Unverified claims (excluded from conclusions)", 11)
        for f in unverified:
            pdf.para(f"- {f.get('title', '')}", 9)
    if body.get("steps"):
        pdf.heading("Steps", 11)
        for s in body["steps"]:
            line = f"{s.get('id')} ({s.get('agent')}): {s.get('status')}"
            if s.get("error"):
                line += f" - {s['error']}"
            pdf.para(line, 9)
    if body.get("disclaimer"):
        pdf.ln(3)
        pdf.para(body["disclaimer"], 8, "I", (110, 110, 110))
    return bytes(pdf.output())
