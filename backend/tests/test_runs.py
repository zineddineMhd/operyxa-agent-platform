from sqlalchemy import select

from operyxa.db.models import Finding, LLMUsage, Report, RunEvent, RunStep, ToolCall
from operyxa.worker.runner import claim_next_run, execute_run

from .conftest import INJECTION, ScriptedLLM, make_run, tool_results


async def rows(env, model, run_id):
    async with env.db.session() as s:
        order = model.id if model is RunEvent else None
        query = select(model).where(model.run_id == run_id)
        return list(
            (await s.execute(query.order_by(order) if order is not None else query)).scalars()
        )


async def test_full_read_only_analysis(env):
    run = await make_run(env)
    assert await claim_next_run(env.db, "w1", 60) == run.id
    await execute_run(run.id, env)

    done = await env.store.get_run(run.id)
    assert done.status == "completed" and done.stop_reason is None and done.commit_sha
    steps = await rows(env, RunStep, run.id)
    assert {s.step_key: s.status for s in steps} == {
        "repo_overview": "completed",
        "security_review": "completed",
    }

    findings = await rows(env, Finding, run.id)
    assert findings and all(f.status == "verified" for f in findings)
    docker = next(f for f in findings if "python:latest" in f.title)
    assert docker.evidence[0]["path"] == "Dockerfile" and docker.evidence[0]["start_line"] == 1

    report = (await rows(env, Report, run.id))[0]
    assert not report.partial and len(report.body["findings"]) == len(findings)
    assert "Dockerfile:1" in report.markdown and done.commit_sha in report.markdown

    types = [e.type for e in await rows(env, RunEvent, run.id)]
    assert types[0] == "tool.requested" and types[-1] == "run.completed"
    for expected in (
        "run.started",
        "plan.validated",
        "agent.started",
        "tool.completed",
        "agent.completed",
        "findings.verified",
        "report.ready",
    ):
        assert expected in types
    usage = await rows(env, LLMUsage, run.id)
    assert {u.step_key for u in usage} == {
        "planner",
        "repo_overview",
        "security_review",
        "reporter",
    }


async def test_prompt_injection_cannot_widen_permissions(env, github):  # T-02
    """A model that fully obeys the injected README still cannot leave its sandbox."""
    obeyed = []

    def handler(names, messages, tool_choice):
        if "github__list_commits" not in names or tool_choice:
            return None
        if any(INJECTION in str(m.content) for m in messages) and not obeyed:
            obeyed.append(True)
            return [
                ("github__get_file", {"path": ".env"}),
                ("shell__exec", {"cmd": "rm -rf /"}),
                ("analysis__scan_dependencies", {}),
                ("github__list_tree", {"owner": "victim", "repo": "secrets"}),
            ]
        return None

    env.llm = ScriptedLLM(handler)
    run = await make_run(env)
    await execute_run(run.id, env)

    assert obeyed, "the injected instructions were delivered to the model"
    calls = await rows(env, ToolCall, run.id)
    rejected = {c.tool: c.error_code for c in calls if c.status == "rejected"}
    assert rejected == {
        "github.get_file": "SENSITIVE_PATH_DENIED",
        "shell.exec": "TOOL_UNKNOWN",
        "analysis.scan_dependencies": "TOOL_PERMISSION_DENIED",
        "github.list_tree": "INVALID_ARGUMENTS",
    }
    assert all(p.startswith("/repos/acme/demo") for p in github.requests)
    assert not any(p.endswith("/contents/.env") for p in github.requests)
    assert "super-secret-value" not in "".join(c.result_text or "" for c in calls)
    assert (await env.store.get_run(run.id)).status == "completed"


async def test_fabricated_finding_is_excluded_from_the_report(env):  # T-10
    def handler(names, messages, tool_choice):
        results = tool_results(messages)
        if "analysis__scan_dependencies" in names and results:
            return [
                (
                    "submit_step_result",
                    {
                        "summary": "done",
                        "findings": [
                            {
                                "title": "SQL injection in login",
                                "severity": "critical",
                                "category": "security",
                                "description": "User input is concatenated into SQL.",
                                "evidence": [
                                    {
                                        "evidence_id": results[0]["evidence_id"],
                                        "quote": "cursor.execute('SELECT * FROM users WHERE id=' + uid)",
                                    }
                                ],
                            }
                        ],
                    },
                )
            ]
        return None

    env.llm = ScriptedLLM(handler)
    run = await make_run(env)
    await execute_run(run.id, env)
    finding = next(f for f in await rows(env, Finding, run.id) if f.agent == "security")
    assert finding.status == "unverified" and finding.evidence[0]["reason"]
    report = (await rows(env, Report, run.id))[0]
    assert "SQL injection" not in str(report.body["findings"])
    assert report.body["unverified_findings"][0]["title"] == "SQL injection in login"


async def test_exceeded_budget_stops_with_a_partial_report(env):  # T-09
    run = await make_run(env, limits={"max_tool_calls": 2})
    await execute_run(run.id, env)
    done = await env.store.get_run(run.id)
    assert done.status == "failed" and done.stop_reason == "budget_exceeded:tool_calls"
    report = (await rows(env, Report, run.id))[0]
    assert report.partial and report.body["stop_reason"] == "budget_exceeded:tool_calls"
    executed = [c for c in await rows(env, ToolCall, run.id) if c.status != "rejected"]
    assert len(executed) == 2
    assert (await rows(env, RunEvent, run.id))[-1].type == "run.failed"


async def test_invalid_plan_is_retried_then_refused(env):  # T-04 end to end
    cyclic = {
        "steps": [
            {"id": "a", "agent": "repository", "action": "inspect the tree", "depends_on": ["b"]},
            {"id": "b", "agent": "security", "action": "scan dependencies", "depends_on": ["a"]},
        ]
    }
    env.llm = ScriptedLLM(
        lambda names, m, c: [("submit_plan", cyclic)] if "submit_plan" in names else None
    )
    run = await make_run(env)
    await execute_run(run.id, env)
    done = await env.store.get_run(run.id)
    assert done.status == "failed" and done.stop_reason == "plan_invalid"
    assert env.llm.calls == 2 and not await rows(env, RunStep, run.id)
    assert [e.type for e in await rows(env, RunEvent, run.id)].count("plan.rejected") == 2


async def test_unreachable_repository_fails_explicitly(env, github):  # T-05
    github.down = True
    run = await make_run(env)
    await execute_run(run.id, env)
    done = await env.store.get_run(run.id)
    assert done.status == "failed" and done.stop_reason == "repository_unavailable"
    assert done.error["code"] == "UPSTREAM_UNAVAILABLE"


async def test_failed_step_skips_dependents_and_reports_partially(env):
    plan = {
        "steps": [
            {"id": "first", "agent": "repository", "action": "inspect the tree", "depends_on": []},
            {
                "id": "second",
                "agent": "security",
                "action": "scan dependencies",
                "depends_on": ["first"],
            },
        ]
    }

    def handler(names, messages, tool_choice):
        if "submit_plan" in names:
            return [("submit_plan", plan)]
        if "github__list_commits" in names:
            raise ConnectionError("model provider unreachable")
        return None

    env.llm = ScriptedLLM(handler)
    run = await make_run(env)
    await execute_run(run.id, env)
    steps = {s.step_key: s.status for s in await rows(env, RunStep, run.id)}
    assert steps == {"first": "failed", "second": "skipped"}
    done = await env.store.get_run(run.id)
    assert done.status == "failed" and done.stop_reason == "step_failed"
    assert (await rows(env, Report, run.id))[0].partial


class WorkerCrash(BaseException):
    """Not an Exception: nothing in the application may swallow it, like a killed process."""


async def test_worker_restart_resumes_from_checkpoint(env):  # T-08
    plan = {
        "steps": [
            {"id": "first", "agent": "repository", "action": "inspect the tree", "depends_on": []},
            {
                "id": "second",
                "agent": "security",
                "action": "scan dependencies",
                "depends_on": ["first"],
            },
        ]
    }
    state = {"crash": True}

    def handler(names, messages, tool_choice):
        if "submit_plan" in names:
            return [("submit_plan", plan)]
        if "analysis__scan_dependencies" in names and tool_results(messages) and state["crash"]:
            raise WorkerCrash()
        return None

    env.llm = ScriptedLLM(handler)
    run = await make_run(env)
    try:
        await execute_run(run.id, env)
    except WorkerCrash:
        pass
    crashed = await env.store.get_run(run.id)
    assert crashed.status == "pending"  # make_run default; a real worker leaves it "running"
    before = await rows(env, ToolCall, run.id)
    llm_calls_before = env.llm.calls

    state["crash"] = False
    await execute_run(run.id, env)
    done = await env.store.get_run(run.id)
    assert done.status == "completed"
    after = await rows(env, ToolCall, run.id)
    # Nothing was executed twice: the finished step is not replayed, tool results are reused.
    assert len(after) == len(before)
    assert len({c.idempotency_key for c in after}) == len(after)
    first_step_usage = [
        u for u in await rows(env, LLMUsage, run.id) if u.step_key in ("planner", "first")
    ]
    assert len(first_step_usage) == 3  # plan + two turns of step "first", never repeated
    assert env.llm.calls - llm_calls_before == 3  # step "second" (2 turns) + reporter
    assert "run.resumed" in [e.type for e in await rows(env, RunEvent, run.id)]


async def test_stale_run_is_reclaimed(env):
    from datetime import timedelta

    from operyxa.db.models import utcnow

    run = await make_run(env)
    assert await claim_next_run(env.db, "w1", 60) == run.id
    assert await claim_next_run(env.db, "w2", 60) is None  # lease still valid
    await env.store.update_run(run.id, heartbeat_at=utcnow() - timedelta(seconds=120))
    assert await claim_next_run(env.db, "w2", 60) == run.id
    assert (await env.store.get_run(run.id)).locked_by == "w2"
