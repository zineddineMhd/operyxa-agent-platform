"""F-10 tool policies and MCP Hub, F-11 exports and metrics, F-12 pull requests with
confirmation, F-13 workflows and teams."""

import uuid

import httpx
import pytest
from sqlalchemy import select

from operyxa.api.app import create_app
from operyxa.api.routes import pull_requests
from operyxa.db.models import Approval, AuditEvent, ToolCall
from operyxa.gateway.gateway import CallContext
from operyxa.mcp_servers.github_writer import GitHubWriter
from operyxa.worker.runner import execute_run

from .conftest import HEAD, make_run
from .test_api import signup

WRITE_TOKEN = "ghp_" + "w" * 36


@pytest.fixture
async def client(settings, db):
    app = create_app(settings, db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def new_project(client, headers, **extra) -> dict:
    response = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "demo", "repository_url": "https://github.com/acme/demo", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def new_run(client, headers, project, **body) -> dict:
    response = await client.post(
        "/api/v1/runs", headers=headers, json={"project_id": project["id"], **body}
    )
    assert response.status_code == 202, response.text
    return response.json()


# -- F-10 ---------------------------------------------------------------------------------


async def test_project_policy_can_disable_or_gate_tools_but_never_loosen(client, env):
    headers = await signup(client, "owner@example.com")
    project = await new_project(client, headers)
    url = f"/api/v1/projects/{project['id']}/tool-policies"
    put = lambda tool, mode: client.put(url, headers=headers, json={"tool": tool, "mode": mode})  # noqa: E731

    assert (await put("github.list_commits", "disabled")).status_code == 200
    tools = {
        t["tool"]: t
        for t in (await put("analysis.check_config", "approval_required")).json()["tools"]
    }
    assert tools["github.list_commits"]["effective"] == "deny"
    assert tools["analysis.check_config"]["effective"] == "approval_required"
    # A high-risk tool keeps its approval whatever the override says.
    assert (await put("test_runner.run_test_profile", "default")).json()["tools"]
    gated = {t["tool"]: t for t in (await client.get(url, headers=headers)).json()["tools"]}
    assert gated["test_runner.run_test_profile"]["effective"] == "approval_required"
    assert (await put("github.get_repo", "disabled")).json()["error"]["code"] == "POLICY_LOCKED"
    assert (await put("shell.exec", "default")).json()["error"]["code"] == "TOOL_UNKNOWN"
    assert (await put("github.get_file", "allow")).status_code == 422

    run = await new_run(client, headers, project, objective="Review the repository, read-only.")
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}", headers=headers)).json()
    assert detail["policy"] == {
        "github.list_commits": "disabled",
        "analysis.check_config": "approval_required",
    }
    # The disabled tool is hidden from the agents and refused if called anyway.
    assert "github.list_commits" not in {
        t.name for t in env.gateway.tools_for("repository", detail["policy"])
    }
    ctx = CallContext(
        uuid.UUID(run["run_id"]),
        "s1",
        "repository",
        {"owner": "acme", "repo": "demo", "ref": HEAD},
        None,
        None,
        detail["policy"],
    )
    outcome = await env.gateway.call(ctx, "github.list_commits", {})
    assert not outcome.ok and outcome.error_code == "TOOL_PERMISSION_DENIED"


async def test_moderate_tool_gated_by_project_waits_for_approval(env):
    run = await make_run(env)
    ctx = CallContext(
        run.id,
        "s1",
        "security",
        {"owner": "acme", "repo": "demo", "ref": HEAD},
        None,
        run.created_by,
        {"analysis.check_config": "approval_required"},
    )
    env.settings.approval_ttl_seconds = 0  # expires at once: the call must not run
    outcome = await env.gateway.call(ctx, "analysis.check_config", {})
    assert not outcome.ok and outcome.error_code == "APPROVAL_EXPIRED"
    async with env.db.session() as s:
        approval = (await s.execute(select(Approval).where(Approval.run_id == run.id))).scalar_one()
    assert approval.tool == "analysis.check_config" and approval.status == "expired"


async def test_policy_changes_need_the_manage_role_and_are_audited(client, db):
    owner = await signup(client, "owner@example.com")
    other = await signup(client, "other@example.com")
    project = await new_project(client, owner)
    url = f"/api/v1/projects/{project['id']}/tool-policies"
    body = {"tool": "github.list_commits", "mode": "disabled"}
    assert (await client.put(url, headers=other, json=body)).status_code == 404
    assert (await client.put(url, headers=owner, json=body)).status_code == 200
    async with db.session() as s:
        actions = set((await s.execute(select(AuditEvent.action))).scalars())
    assert "tool_policy.updated" in actions


async def test_mcp_hub_lists_and_probes_servers(client):
    headers = await signup(client, "a@example.com")
    listing = (await client.get("/api/v1/mcp/servers", headers=headers)).json()["servers"]
    names = {s["name"]: s for s in listing}
    assert names["github"]["transport"] == "in-process" and not names["test_runner"]["enabled"]
    probed = (await client.get("/api/v1/mcp/servers?probe=true", headers=headers)).json()["servers"]
    github = next(s for s in probed if s["name"] == "github")
    assert github["status"] == "ok" and "github.get_file" in github["available_tools"]
    assert github["ignored_tools"] == [] and github["missing_tools"] == []


# -- F-11 ---------------------------------------------------------------------------------


async def test_report_exports_and_metrics(client, env):
    headers = await signup(client, "a@example.com")
    project = await new_project(client, headers)
    run = await new_run(client, headers, project, objective="Review the repository, read-only.")
    await execute_run(uuid.UUID(run["run_id"]), env)
    url = f"/api/v1/runs/{run['run_id']}"

    pdf = await client.get(f"{url}/report.pdf", headers=headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"
    assert "attachment" in pdf.headers["content-disposition"]
    data = (await client.get(f"{url}/report.json", headers=headers)).json()
    assert data["repository"] == "acme/demo" and data["findings"]
    md = await client.get(f"{url}/report.md?download=true", headers=headers)
    assert md.headers["content-disposition"].endswith('.md"')

    metrics = (await client.get(f"{url}/metrics", headers=headers)).json()
    assert metrics["duration_seconds"] is not None and metrics["usage"]["llm_calls"] >= 4
    assert metrics["verification_rate"] == 1.0 and metrics["verified_by_severity"]
    assert metrics["budget_used"]["max_llm_calls"] > 0
    assert sum(metrics["models"].values()) == metrics["usage"]["llm_calls"]
    assert {t["tool"] for t in metrics["tools"]} >= {
        "github.get_repo",
        "analysis.scan_dependencies",
    }

    overview = (await client.get("/api/v1/analytics/overview", headers=headers)).json()
    assert overview["verification_rate"] == 1.0
    assert overview["runs_by_workflow"] == {"repository_analysis": 1}
    assert overview["avg_duration_seconds"] is not None

    other = await signup(client, "b@example.com")
    assert (await client.get(f"{url}/report.pdf", headers=other)).status_code == 404


# -- F-13 workflows -----------------------------------------------------------------------


async def test_builtin_workflow_narrows_the_agents(client, env):
    headers = await signup(client, "a@example.com")
    project = await new_project(client, headers)
    workflows = (await client.get("/api/v1/workflows", headers=headers)).json()
    assert {w["key"] for w in workflows["builtin"]} >= {"security_audit", "remediation"}
    run = await new_run(client, headers, project, workflow_type="security_audit")
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}", headers=headers)).json()
    assert detail["options"]["agents"] == ["security"] and "acme/demo" in detail["objective"]
    await execute_run(uuid.UUID(run["run_id"]), env)
    steps = (await client.get(f"/api/v1/runs/{run['run_id']}/steps", headers=headers)).json()
    assert [s["agent"] for s in steps] == ["security"]
    assert (await client.get(f"/api/v1/runs/{run['run_id']}", headers=headers)).json()[
        "status"
    ] == "completed"


async def test_saved_workflow_is_reusable_and_cannot_add_agents(client):
    headers = await signup(client, "a@example.com")
    project = await new_project(client, headers)
    bad = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "name": "x",
            "base_type": "security_audit",
            "objective_template": "Look at {repository} code",
            "agents": ["repository"],
        },
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "WORKFLOW_INVALID"
    created = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "name": "Deps only",
            "base_type": "repository_analysis",
            "objective_template": "Check the dependencies of {repository} only.",
            "agents": ["security"],
        },
    )
    assert created.status_code == 201, created.text
    workflow = created.json()
    run = await new_run(client, headers, project, workflow_id=workflow["id"])
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}", headers=headers)).json()
    assert detail["objective"] == "Check the dependencies of acme/demo only."
    assert detail["options"]["agents"] == ["security"] and detail["workflow_id"] == workflow["id"]
    other = await signup(client, "b@example.com")
    other_project = await new_project(client, other)
    stolen = await client.post(
        "/api/v1/runs",
        headers=other,
        json={"project_id": other_project["id"], "workflow_id": workflow["id"]},
    )
    assert stolen.status_code == 404


# -- F-13 teams ---------------------------------------------------------------------------


async def test_team_roles_control_access(client):
    owner = await signup(client, "owner@example.com")
    viewer = await signup(client, "viewer@example.com")
    member = await signup(client, "member@example.com")
    stranger = await signup(client, "stranger@example.com")
    team = (await client.post("/api/v1/teams", headers=owner, json={"name": "Core"})).json()
    base = f"/api/v1/teams/{team['id']}/members"
    assert (
        await client.post(
            base, headers=owner, json={"email": "viewer@example.com", "role": "viewer"}
        )
    ).status_code == 200
    added = await client.post(base, headers=owner, json={"email": "member@example.com"})
    assert {m["role"] for m in added.json()["members"]} == {"owner", "viewer", "member"}
    assert (
        await client.post(base, headers=owner, json={"email": "nobody@example.com"})
    ).status_code == 404
    # Only an owner or admin manages the team.
    assert (
        await client.post(base, headers=member, json={"email": "stranger@example.com"})
    ).status_code == 403

    project = await new_project(client, owner, team_id=team["id"])
    run = await new_run(client, owner, project, objective="Review the repository, read-only.")

    seen = (await client.get("/api/v1/projects", headers=viewer)).json()["items"]
    assert [p["id"] for p in seen] == [project["id"]]
    assert (await client.get(f"/api/v1/runs/{run['run_id']}", headers=viewer)).status_code == 200
    denied = await client.post(
        "/api/v1/runs",
        headers=viewer,
        json={"project_id": project["id"], "objective": "Review it all please."},
    )
    assert denied.status_code == 403
    assert (
        await client.post(f"/api/v1/runs/{run['run_id']}/cancel", headers=viewer)
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/runs",
            headers=member,
            json={"project_id": project["id"], "objective": "Review it all please."},
        )
    ).status_code == 202
    policy = {"tool": "github.list_commits", "mode": "disabled"}
    assert (
        await client.put(
            f"/api/v1/projects/{project['id']}/tool-policies", headers=member, json=policy
        )
    ).status_code == 403
    assert (await client.get(f"/api/v1/runs/{run['run_id']}", headers=stranger)).status_code == 404
    assert (await client.get("/api/v1/projects", headers=stranger)).json()["total"] == 0

    member_id = next(m["user_id"] for m in added.json()["members"] if m["role"] == "member")
    assert (await client.delete(f"{base}/{member_id}", headers=owner)).status_code == 200
    assert (await client.get(f"/api/v1/runs/{run['run_id']}", headers=member)).status_code == 404
    owner_id = next(m["user_id"] for m in added.json()["members"] if m["role"] == "owner")
    assert (await client.delete(f"{base}/{owner_id}", headers=owner)).status_code == 409


async def test_viewer_cannot_decide_an_approval(client, env, db):
    owner = await signup(client, "owner@example.com")
    viewer = await signup(client, "viewer@example.com")
    team = (await client.post("/api/v1/teams", headers=owner, json={"name": "Core"})).json()
    await client.post(
        f"/api/v1/teams/{team['id']}/members",
        headers=owner,
        json={"email": "viewer@example.com", "role": "viewer"},
    )
    project = await new_project(client, owner, team_id=team["id"])
    run = await new_run(client, owner, project, objective="Review the repository, read-only.")
    async with db.session.begin() as s:
        approval = Approval(
            run_id=uuid.UUID(run["run_id"]),
            step_key="s",
            agent="testing",
            tool="t",
            args={},
            args_hash="h" * 64,
            risk="high",
            expires_at=__import__("operyxa.db.models", fromlist=["utcnow"])
            .utcnow()
            .replace(year=2099),
        )
        s.add(approval)
    url = f"/api/v1/approvals/{approval.id}/decision"
    listed = (await client.get("/api/v1/approvals", headers=viewer)).json()
    assert [a["id"] for a in listed] == [str(approval.id)]
    assert (await client.post(url, headers=viewer, json={"decision": "approve"})).status_code == 403
    assert (await client.post(url, headers=owner, json={"decision": "approve"})).status_code == 200


# -- F-12 ---------------------------------------------------------------------------------


class FakeWriter:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, settings, token):
        assert token == WRITE_TOKEN  # the separate write token, decrypted only here
        return self

    async def create_pull_request(self, owner, repo, **kwargs):
        self.calls.append({"owner": owner, "repo": repo, **kwargs})
        return {"number": 7, "url": "https://github.com/acme/demo/pull/7", "commit_sha": "b" * 40}


async def test_fix_proposal_to_pull_request_needs_a_confirmation(client, env, monkeypatch):
    writer = FakeWriter()
    monkeypatch.setattr(pull_requests, "writer_factory", writer)
    headers = await signup(client, "a@example.com")
    project = await new_project(client, headers)
    run = await new_run(client, headers, project, workflow_type="remediation")
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}", headers=headers)).json()
    assert detail["options"]["propose_fixes"] and "remediation" in detail["options"]["agents"]
    await execute_run(uuid.UUID(run["run_id"]), env)
    url = f"/api/v1/runs/{run['run_id']}"
    assert (await client.get(url, headers=headers)).json()["status"] == "completed"

    # Nothing was written while the agents ran.
    assert writer.calls == []
    changes = (await client.get(f"{url}/changes", headers=headers)).json()["changes"]
    assert [c["path"] for c in changes] == ["Dockerfile"]
    assert (
        "-FROM python:latest" in changes[0]["diff"]
        and "+FROM python:3.12-slim" in changes[0]["diff"]
    )

    prepared = await client.post(
        f"{url}/pull-requests", headers=headers, json={"title": "Pin the base image"}
    )
    assert prepared.status_code == 201, prepared.text
    proposal = prepared.json()
    assert proposal["status"] == "awaiting_confirmation" and proposal["branch"].startswith(
        "operyxa/run-"
    )
    confirm = f"/api/v1/pull-requests/{proposal['id']}/confirm"

    missing = await client.post(
        confirm, headers=headers, json={"content_hash": proposal["content_hash"]}
    )
    assert missing.json()["error"]["code"] == "WRITE_TOKEN_MISSING"
    patched = await client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=headers,
        json={"github_write_token": WRITE_TOKEN},
    )
    assert patched.json()["has_write_token"] and WRITE_TOKEN not in patched.text

    wrong = await client.post(confirm, headers=headers, json={"content_hash": "0" * 64})
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "CONFIRMATION_MISMATCH"
    other = await signup(client, "b@example.com")
    assert (
        await client.post(confirm, headers=other, json={"content_hash": proposal["content_hash"]})
    ).status_code == 404

    done = await client.post(
        confirm, headers=headers, json={"content_hash": proposal["content_hash"]}
    )
    assert done.status_code == 200 and done.json()["status"] == "created"
    assert done.json()["pr_url"].endswith("/pull/7")
    assert len(writer.calls) == 1 and writer.calls[0]["base_sha"] == HEAD
    assert writer.calls[0]["files"][0]["path"] == "Dockerfile"
    again = await client.post(
        confirm, headers=headers, json={"content_hash": proposal["content_hash"]}
    )
    assert again.status_code == 409 and len(writer.calls) == 1

    async with env.db.session() as s:
        actions = list((await s.execute(select(AuditEvent.action))).scalars())
        stored = list((await s.execute(select(ToolCall.args))).scalars())
    assert "pull_request.confirmed" in actions and "pull_request.created" in actions
    assert WRITE_TOKEN not in str(stored)


async def test_pull_requests_refuse_unfinished_runs_and_empty_proposals(client, env):
    headers = await signup(client, "a@example.com")
    project = await new_project(client, headers)
    run = await new_run(client, headers, project, objective="Review the repository, read-only.")
    url = f"/api/v1/runs/{run['run_id']}/pull-requests"
    assert (await client.post(url, headers=headers, json={})).json()["error"][
        "code"
    ] == "RUN_NOT_COMPLETED"
    await execute_run(uuid.UUID(run["run_id"]), env)
    assert (await client.post(url, headers=headers, json={})).json()["error"][
        "code"
    ] == "NOTHING_TO_PROPOSE"
    with_report = await client.post(url, headers=headers, json={"include_report": True})
    assert with_report.status_code == 201
    assert with_report.json()["files"][0]["path"].startswith("docs/operyxa/report-")
    cancelled = await client.post(
        f"/api/v1/pull-requests/{with_report.json()['id']}/cancel", headers=headers
    )
    assert cancelled.json()["status"] == "cancelled"


async def test_proposal_tool_refuses_dangerous_changes(env, github):
    github.files["config.py"] = 'DB_PASSWORD = "correct horse battery"\n'
    run = await make_run(env)
    ctx = CallContext(
        run.id,
        "fix",
        "remediation",
        {"owner": "acme", "repo": "demo", "ref": HEAD},
        None,
        run.created_by,
    )
    propose = lambda path, content: env.gateway.call(  # noqa: E731
        ctx, "changes.propose_file_change", {"path": path, "new_content": content, "rationale": "r"}
    )
    cases = {
        ".github/workflows/ci.yml": ("on: push\n", "PATH_PROTECTED"),
        "config.py": ("DB_PASSWORD = None\n", "FILE_HAS_SECRETS"),
        "app.py": ("TOKEN = '[REDACTED:secret]'\n", "CONTAINS_SECRET"),
        ".env": ("X=1\n", "SENSITIVE_PATH_DENIED"),
        "../escape.py": ("x = 1\n", "INVALID_ARGUMENT"),
    }
    for path, (content, code) in cases.items():
        outcome = await propose(path, content)
        assert not outcome.ok and code in f"{outcome.error_code} {outcome.text}", (path, outcome)
    ok = await propose("NEW.md", "# hello\n")
    assert ok.ok and '"change_type": "add"' in ok.text
    # Other agents never see the tool.
    assert "changes.propose_file_change" not in {t.name for t in env.gateway.tools_for("security")}


async def test_github_writer_creates_a_branch_commit_and_draft_pr():
    seen: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        assert request.headers["authorization"] == f"Bearer {WRITE_TOKEN}"
        path = request.url.path
        if request.method == "GET" and path == "/repos/acme/demo":
            return httpx.Response(200, json={"default_branch": "main"})
        if path.startswith("/repos/acme/demo/git/commits/"):
            return httpx.Response(200, json={"tree": {"sha": "t0"}})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "t1"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "c1"})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if path.endswith("/pulls"):
            return httpx.Response(
                201, json={"number": 3, "html_url": "https://github.com/acme/demo/pull/3"}
            )
        return httpx.Response(404)

    writer = GitHubWriter(
        httpx.AsyncClient(
            base_url="https://api.github.test", transport=httpx.MockTransport(handler)
        ),
        WRITE_TOKEN,
    )
    result = await writer.create_pull_request(
        "acme",
        "demo",
        base_sha=HEAD,
        branch="operyxa/run-1",
        title="t",
        body="b",
        files=[{"path": "Dockerfile", "content": "FROM python:3.12-slim\n"}],
    )
    assert result == {"number": 3, "url": "https://github.com/acme/demo/pull/3", "commit_sha": "c1"}
    methods = [(m, p.rsplit("/", 1)[-1]) for m, p, _ in seen]
    assert methods[-1] == ("POST", "pulls") and seen[-1][2]["draft"] is True
    assert seen[2][2]["base_tree"] == "t0" and seen[3][2]["parents"] == [HEAD]
    assert seen[4][2] == {"ref": "refs/heads/operyxa/run-1", "sha": "c1"}
    assert not any(m in ("PUT", "PATCH", "DELETE") for m, _, _ in seen)  # never moves a branch
