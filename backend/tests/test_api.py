import httpx
import pytest

from operyxa.api.app import create_app
from operyxa.worker.runner import execute_run


@pytest.fixture
async def client(settings, db):
    app = create_app(settings, db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def signup(client, email) -> dict[str, str]:
    body = {"email": email, "password": "a-long-password"}
    assert (await client.post("/api/v1/auth/register", json=body)).status_code == 201
    token = (await client.post("/api/v1/auth/login", json=body)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def start(client, headers, **extra_headers):
    project = (
        await client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "demo", "repository_url": "https://github.com/acme/demo"},
        )
    ).json()
    response = await client.post(
        "/api/v1/runs",
        headers={**headers, **extra_headers},
        json={"project_id": project["id"], "objective": "Review the repository, read-only."},
    )
    assert response.status_code == 202, response.text
    return project, response.json()


async def test_auth_flow_and_error_model(client):
    headers = await signup(client, "a@example.com")
    assert (await client.get("/api/v1/users/me", headers=headers)).json()[
        "email"
    ] == "a@example.com"
    again = await client.post(
        "/api/v1/auth/register", json={"email": "A@example.com", "password": "a-long-password"}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "EMAIL_TAKEN"
    bad = await client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "nope"}
    )
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    anonymous = await client.get("/api/v1/projects")
    assert anonymous.status_code == 401 and anonymous.json()["error"]["request_id"]
    invalid = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "x", "repository_url": "https://evil.example/a/b"},
    )
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_user_cannot_reach_another_users_resources(client):  # T-01
    alice, bob = await signup(client, "alice@example.com"), await signup(client, "bob@example.com")
    project, run = await start(client, alice)
    run_url = f"/api/v1/runs/{run['run_id']}"
    for path in (
        f"/api/v1/projects/{project['id']}",
        run_url,
        f"{run_url}/steps",
        f"{run_url}/tool-calls",
        f"{run_url}/findings",
        f"{run_url}/report",
        f"{run_url}/events",
        f"{run_url}/events/history",
        f"{run_url}/evidence/ev_x",
    ):
        assert (await client.get(path, headers=bob)).status_code == 404, path
    assert (await client.post(f"{run_url}/cancel", headers=bob)).status_code == 404
    foreign = await client.post(
        "/api/v1/runs",
        headers=bob,
        json={"project_id": project["id"], "objective": "Review the repository, read-only."},
    )
    assert foreign.status_code == 404
    assert (await client.get("/api/v1/runs", headers=bob)).json()["total"] == 0
    assert (await client.get("/api/v1/runs", headers=alice)).json()["total"] == 1


async def test_run_creation_is_idempotent_and_options_are_locked(client):
    headers = await signup(client, "a@example.com")
    project, first = await start(client, headers, **{"Idempotency-Key": "k1"})
    replay = await client.post(
        "/api/v1/runs",
        headers={**headers, "Idempotency-Key": "k1"},
        json={"project_id": project["id"], "objective": "Review the repository, read-only."},
    )
    assert replay.status_code == 202 and replay.json()["run_id"] == first["run_id"]
    assert first["events_url"] == f"/api/v1/runs/{first['run_id']}/events"
    writable = await client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "project_id": project["id"],
            "objective": "Rewrite the main branch please.",
            "options": {"read_only": False},
        },
    )
    assert writable.status_code == 422


async def test_cancel_pending_run(client):
    headers = await signup(client, "a@example.com")
    _, run = await start(client, headers)
    cancelled = await client.post(f"/api/v1/runs/{run['run_id']}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    again = await client.post(f"/api/v1/runs/{run['run_id']}/cancel", headers=headers)
    assert again.status_code == 409 and again.json()["error"]["code"] == "RUN_NOT_CANCELLABLE"


async def test_run_results_and_sse_replay(client, env):
    headers = await signup(client, "a@example.com")
    _, run = await start(client, headers)
    url = f"/api/v1/runs/{run['run_id']}"
    assert (await client.get(f"{url}/report", headers=headers)).json()["error"][
        "code"
    ] == "REPORT_NOT_READY"

    import uuid

    await execute_run(uuid.UUID(run["run_id"]), env)

    detail = (await client.get(url, headers=headers)).json()
    assert detail["status"] == "completed" and detail["usage"]["llm_calls"] >= 4
    assert len((await client.get(f"{url}/steps", headers=headers)).json()) == 2
    findings = (await client.get(f"{url}/findings?status=verified", headers=headers)).json()
    evidence_id = findings[0]["evidence"][0]["evidence_id"]
    evidence = (await client.get(f"{url}/evidence/{evidence_id}", headers=headers)).json()
    assert findings[0]["evidence"][0]["quote"].split()[0] in evidence["result_text"]
    markdown = await client.get(f"{url}/report.md", headers=headers)
    assert (
        markdown.headers["content-type"].startswith("text/markdown")
        and "# Review report" in markdown.text
    )

    stream = (await client.get(f"{url}/events", headers=headers)).text
    assert "event: run.created" in stream and stream.rstrip().endswith(
        'data: {"status": "completed"}'
    )
    ids = [int(line[4:]) for line in stream.splitlines() if line.startswith("id: ")]
    assert ids == sorted(ids)
    resumed = (
        await client.get(f"{url}/events", headers={**headers, "Last-Event-ID": str(ids[-3])})
    ).text
    assert [int(line[4:]) for line in resumed.splitlines() if line.startswith("id: ")] == ids[-2:]

    overview = (await client.get("/api/v1/analytics/overview", headers=headers)).json()
    assert overview["runs_total"] == 1 and overview["success_rate"] == 1.0
    tools = (await client.get("/api/v1/mcp/tools", headers=headers)).json()["tools"]
    assert len(tools) == 10
    assert {t["tool"] for t in tools if t["access"] != "read-only"} == {
        "test_runner.run_test_profile"
    }


async def test_project_token_is_write_only(client, db):
    from sqlalchemy import select

    from operyxa.db.models import Project

    headers = await signup(client, "a@example.com")
    token = "github_pat_" + "x" * 40
    created = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "private", "repository_url": "acme/private", "github_token": token},
    )
    assert created.status_code == 201 and created.json()["has_token"] is True
    listing = await client.get("/api/v1/projects", headers=headers)
    assert token not in created.text + listing.text and "encrypted" not in listing.text
    async with db.session() as s:
        stored = (await s.execute(select(Project.github_token_encrypted))).scalar_one()
    assert stored and token not in stored
