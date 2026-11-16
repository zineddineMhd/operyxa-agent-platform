import json

from sqlalchemy import select

from operyxa.db.models import AuditEvent, ToolCall
from operyxa.gateway.gateway import CallContext

from .conftest import HEAD, make_run

SCOPE = {"owner": "acme", "repo": "demo", "ref": HEAD}


async def ctx_for(env, agent="repository"):
    run = await make_run(env)
    return CallContext(run.id, "s1", agent, SCOPE, None, run.created_by)


async def test_read_file_returns_evidence_and_redacts_secrets(env):
    ctx = await ctx_for(env)
    outcome = await env.gateway.call(ctx, "github.get_file", {"path": "app.py"})
    assert outcome.ok and outcome.evidence_id.startswith("ev_")
    assert "AKIAABCDEFGHIJKLMNOP" not in outcome.text and "REDACTED" in outcome.text
    assert 'trust="untrusted_data"' in outcome.for_model()


async def test_unauthorized_calls_are_rejected_and_audited(env, github):  # T-03
    ctx = await ctx_for(env)
    cases = {
        ("shell.exec", "TOOL_UNKNOWN"): {"cmd": "rm -rf /"},
        ("analysis.scan_dependencies", "TOOL_PERMISSION_DENIED"): {},  # not this agent's tool
        ("github.get_file", "SENSITIVE_PATH_DENIED"): {"path": ".env"},
        ("github.list_tree", "INVALID_ARGUMENTS"): {"owner": "victim", "repo": "secrets"},
    }
    before = len(github.requests)
    for (tool, code), args in cases.items():
        outcome = await env.gateway.call(ctx, tool, args)
        assert not outcome.ok and outcome.error_code == code, tool
    assert len(github.requests) == before  # nothing reached the connector
    async with env.db.session() as s:
        rejected = (
            (await s.execute(select(ToolCall).where(ToolCall.run_id == ctx.run_id))).scalars().all()
        )
        audits = (
            (await s.execute(select(AuditEvent).where(AuditEvent.resource_id == str(ctx.run_id))))
            .scalars()
            .all()
        )
    assert {c.status for c in rejected} == {"rejected"} and len(rejected) == 4
    assert len(audits) == 4 and audits[0].actor_id == ctx.actor_id


async def test_scope_is_injected_not_requested(env, github):
    ctx = await ctx_for(env)
    assert (await env.gateway.call(ctx, "github.list_tree", {})).ok
    assert all(p.startswith("/repos/acme/demo") for p in github.requests)
    schema = next(t for t in env.gateway.tools_for("repository") if t.name == "github.get_file")
    assert "owner" not in schema.input_schema["properties"]


async def test_missing_file_is_a_verifiable_error_without_retry(env):
    ctx = await ctx_for(env)
    outcome = await env.gateway.call(ctx, "github.get_file", {"path": "nope.txt"})
    assert not outcome.ok and outcome.error_code == "NOT_FOUND"
    async with env.db.session() as s:
        call = (await s.execute(select(ToolCall).where(ToolCall.run_id == ctx.run_id))).scalar_one()
    assert call.status == "failed" and call.attempts == 1


async def test_unavailable_connector_fails_explicitly_then_breaker_opens(env, github):  # T-05
    ctx = await ctx_for(env)
    github.down = True
    first = await env.gateway.call(ctx, "github.list_tree", {})
    assert not first.ok and first.error_code == "UPSTREAM_UNAVAILABLE"
    calls = len(github.requests)
    second = await env.gateway.call(ctx, "github.list_commits", {})
    assert second.error_code == "TOOL_UNAVAILABLE" and len(github.requests) == calls


async def test_identical_call_is_served_from_the_run_cache(env, github):
    ctx = await ctx_for(env)
    first = await env.gateway.call(ctx, "github.get_file", {"path": "README.md"})
    calls = len(github.requests)
    second = await env.gateway.call(ctx, "github.get_file", {"path": "README.md"})
    assert second.evidence_id == first.evidence_id and len(github.requests) == calls


async def test_scanners_report_located_issues(env):
    ctx = await ctx_for(env, "security")
    deps = json.loads((await env.gateway.call(ctx, "analysis.scan_dependencies", {})).text)
    rules = {(i["rule"], i["line"]) for i in deps["issues"]}
    assert ("unpinned-dependency", 2) in rules and ("unpinned-dependency", 3) in rules
    config = json.loads((await env.gateway.call(ctx, "analysis.check_config", {})).text)
    found = {i["rule"] for i in config["issues"]}
    assert {
        "env-file-committed",
        "docker-unpinned-base-image",
        "docker-runs-as-root",
        "workflow-pull-request-target",
        "workflow-action-on-branch",
        "no-tests",
    } <= found
