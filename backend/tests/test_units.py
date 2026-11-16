from types import SimpleNamespace

from operyxa.agent.evidence import verify_finding, verify_reference
from operyxa.agent.plan import validate_plan
from operyxa.gateway.catalog import is_sensitive_path
from operyxa.gateway.redaction import redact

AGENTS = {"repository", "security"}


def step(id_, agent="repository", deps=()):
    return {"id": id_, "agent": agent, "action": "do something useful", "depends_on": list(deps)}


def test_valid_plan():
    plan, errors = validate_plan(
        {"steps": [step("a"), step("b", "security", ["a"])]}, max_steps=6, allowed_agents=AGENTS
    )
    assert errors == [] and [s["id"] for s in plan["steps"]] == ["a", "b"]


def test_cyclic_plan_is_refused():  # T-04
    plan, errors = validate_plan(
        {"steps": [step("a", deps=["b"]), step("b", deps=["a"])]},
        max_steps=6,
        allowed_agents=AGENTS,
    )
    assert plan is None and "dependencies must be acyclic" in errors


def test_plan_rejects_unknown_agent_dependency_and_size():
    _, errors = validate_plan({"steps": [step("a", "testing")]}, max_steps=6, allowed_agents=AGENTS)
    assert errors
    _, errors = validate_plan(
        {"steps": [step("a", deps=["ghost"])]}, max_steps=6, allowed_agents=AGENTS
    )
    assert any("unknown step" in e for e in errors)
    _, errors = validate_plan(
        {"steps": [step(f"s{i}") for i in range(7)]}, max_steps=6, allowed_agents=AGENTS
    )
    assert any("too many steps" in e for e in errors)
    _, errors = validate_plan({"steps": [step("a"), step("a")]}, max_steps=6, allowed_agents=AGENTS)
    assert "step ids must be unique" in errors


def call(tool, text, **args):
    return SimpleNamespace(tool=tool, result_text=text, args=args)


def test_evidence_location_is_derived_from_tool_output():
    calls = {
        "ev_1": call(
            "github.get_file",
            '{"path": "src/app.py", "start_line": 10, '
            '"content": "import os\\nDEBUG = True\\nprint(1)"}',
            path="src/app.py",
        )
    }
    ref = verify_reference({"evidence_id": "ev_1", "quote": "DEBUG = True"}, calls)
    assert ref["verified"] and ref["path"] == "src/app.py" and ref["start_line"] == 11


def test_evidence_from_scanner_issue_uses_issue_path_and_line():
    calls = {
        "ev_2": call(
            "analysis.check_config",
            '{"issues": [{"rule": "x", "path": "Dockerfile",'
            ' "line": 3, "snippet": "FROM python:latest", "message": "m"}]}',
        )
    }
    ref = verify_reference({"evidence_id": "ev_2", "quote": "FROM  python:latest"}, calls)
    assert ref["verified"] and ref["path"] == "Dockerfile" and ref["start_line"] == 3


def test_fabricated_evidence_is_unverified():  # T-10
    calls = {
        "ev_1": call("github.get_file", '{"path": "a.py", "start_line": 1, "content": "x = 1"}')
    }
    assert (
        verify_finding([{"evidence_id": "ev_1", "quote": "eval(user_input)"}], calls)[0]
        == "unverified"
    )
    assert verify_finding([{"evidence_id": "ev_404", "quote": "x = 1"}], calls)[0] == "unverified"
    assert verify_finding([], calls)[0] == "unverified"
    assert verify_finding([{"evidence_id": "ev_1", "quote": "x = 1"}], calls)[0] == "verified"


def test_redaction():
    text = redact(
        "key=AKIAABCDEFGHIJKLMNOP\npassword: 'hunter2hunter2'\n"
        "url=https://user:pass1234@host/db\nname = demo"
    )
    assert "AKIAABCDEFGHIJKLMNOP" not in text and "hunter2" not in text and "pass1234" not in text
    assert "name = demo" in text


def test_sensitive_paths():
    assert is_sensitive_path(".env") and is_sensitive_path("config/.env.production")
    assert is_sensitive_path("deploy/server.pem") and is_sensitive_path("id_rsa")
    assert not is_sensitive_path(".env.example") and not is_sensitive_path("src/environment.py")
