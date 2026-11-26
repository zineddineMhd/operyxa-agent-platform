"""Regressions for the four defects seen in real runs (Gemini and NVIDIA)."""

import json

from sqlalchemy import select

from operyxa.agent.evidence import verify_reference
from operyxa.db.models import RunEvent, ToolCall
from operyxa.gateway.gateway import CallContext
from operyxa.gateway.redaction import redact, redact_tool_output
from operyxa.gateway.truncation import truncate_output

from .conftest import HEAD, make_run
from .test_units import call

SCOPE = {"owner": "acme", "repo": "demo", "ref": HEAD}
QUOTED = (
    'DB_PASSWORD = "correct horse battery"\n'
    "api_token = 'tok-1234567890abcdef'\n"
    'config = {"api_key": "AIzaNotAGoogleKeyButSecret"}\n'
    'name = "demo"\n'
)


def test_quoted_secrets_are_masked_in_decoded_json():
    raw = json.dumps({"path": "settings.py", "start_line": 1, "content": QUOTED})
    out = redact_tool_output(raw)
    for secret in ("correct horse battery", "tok-1234567890abcdef", "AIzaNotAGoogleKeyButSecret"):
        assert secret not in out
    data = json.loads(out)
    assert 'DB_PASSWORD = "[REDACTED:secret]"' in data["content"]
    assert 'name = "demo"' in data["content"]


def test_placeholders_and_new_key_formats():
    text = redact(
        'password = "changeme"\nLLM_API_KEY=nvapi-abcdefghijklmnopqrstuvwxyz012345\n'
        "OPENAI=sk-proj-abcdefghijklmnopqrstuvwxyz\ntoken = '${GITHUB_TOKEN}'\n"
    )
    assert 'password = "changeme"' in text and "${GITHUB_TOKEN}" in text
    assert "nvapi-abc" not in text and "sk-proj-abc" not in text


async def test_quoted_secret_never_reaches_model_or_database(env, github):
    github.files["settings.py"] = QUOTED
    run = await make_run(env)
    ctx = CallContext(run.id, "s1", "security", SCOPE, None, run.created_by)
    outcome = await env.gateway.call(ctx, "github.get_file", {"path": "settings.py"})
    assert outcome.ok
    assert "correct horse battery" not in outcome.for_model()
    async with env.db.session() as s:
        stored = (await s.execute(select(ToolCall).where(ToolCall.run_id == run.id))).scalar_one()
        events = (await s.execute(select(RunEvent).where(RunEvent.run_id == run.id))).scalars()
        dumped = stored.result_text + json.dumps([e.data for e in events])
    assert "correct horse battery" not in dumped and "tok-1234567890abcdef" not in dumped


def test_private_key_keeps_its_line_count():
    content = (
        "line1\n-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB\nCCCC\n"
        "-----END RSA PRIVATE KEY-----\nDEBUG = True\n"
    )
    masked = redact(content)
    assert "AAAA" not in masked and masked.count("\n") == content.count("\n")
    raw = redact_tool_output(json.dumps({"path": "k.py", "start_line": 1, "content": content}))
    ref = verify_reference(
        {"evidence_id": "ev_1", "quote": "DEBUG = True"}, {"ev_1": call("github.get_file", raw)}
    )
    assert ref["verified"] and ref["start_line"] == 7


def test_truncation_keeps_valid_json_and_line_numbers():
    content = "\n".join(f"line {i} " + "x" * 80 for i in range(1, 2001))
    raw = json.dumps({"path": "big.py", "start_line": 1, "total_lines": 2000, "content": content})
    out, truncated = truncate_output(raw, 40_000)
    assert truncated and len(out) <= 40_000
    data = json.loads(out)  # still valid JSON
    assert data["gateway_truncated"] and data["path"] == "big.py"
    ref = verify_reference(
        {"evidence_id": "ev_1", "quote": "line 300 " + "x" * 10},
        {"ev_1": call("github.get_file", out, path="big.py")},
    )
    assert ref["verified"] and ref["path"] == "big.py" and ref["start_line"] == 300


def test_truncation_of_long_lists_and_plain_text():
    raw = json.dumps({"entries": [{"path": f"f{i}.py"} for i in range(5000)], "total": 5000})
    out, truncated = truncate_output(raw, 10_000)
    assert truncated and len(out) <= 10_000 and json.loads(out)["total"] == 5000
    out, truncated = truncate_output("y" * 50, 10)
    assert truncated and out.startswith("y" * 10)
    assert truncate_output("short", 10) == ("short", False)


def test_quote_of_guessed_secret_is_verified_against_masked_text():
    stored = redact_tool_output(
        json.dumps(
            {
                "path": "app.py",
                "start_line": 1,
                "content": "x = 1\nAPI_KEY = 'AKIAABCDEFGHIJKLMNOP'\n",
            }
        )
    )
    calls = {"ev_1": call("github.get_file", stored, path="app.py")}
    # The model "remembers" the original, or invents a plausible one.
    for quote in ("API_KEY = 'AKIAABCDEFGHIJKLMNOP'", "API_KEY = 'AKIAZZZZZZZZZZZZZZZZ'"):
        ref = verify_reference({"evidence_id": "ev_1", "quote": quote}, calls)
        assert ref["verified"] and ref["start_line"] == 2, quote
        assert "AKIA" not in ref["quote"]
    # A quote of a line that does not exist stays unverified.
    ref = verify_reference({"evidence_id": "ev_1", "quote": "SECRET = 'hunter2hunter2'"}, calls)
    assert not ref["verified"] and "hunter2" not in ref["quote"]
