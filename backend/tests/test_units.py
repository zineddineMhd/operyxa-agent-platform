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


def test_tool_schema_keeps_properties_named_title():
    from operyxa.agent.react import SUBMIT_TOOL

    finding = SUBMIT_TOOL["function"]["parameters"]["properties"]["findings"]["items"]
    assert set(finding["required"]) <= set(finding["properties"])
    assert "title" in finding["properties"] and "$ref" not in str(SUBMIT_TOOL)


async def test_llm_retries_transient_errors_then_falls_back():
    import pytest
    from langchain_core.messages import AIMessage

    from operyxa.errors import LLMUnavailable
    from operyxa.llm.base import ResilientLLM

    class Flaky:
        def __init__(self, name, errors):
            self.model_name, self.errors, self.calls = name, list(errors), 0

        async def invoke(self, messages, tools, tool_choice=None):
            self.calls += 1
            if self.errors:
                raise self.errors.pop(0)
            return AIMessage(content="ok")

    busy = RuntimeError("503 UNAVAILABLE high demand")
    primary, backup = Flaky("main", [busy, busy, busy]), Flaky("backup", [])
    ai = await ResilientLLM([primary, backup], 1, 0).invoke([], [])
    assert ai.response_metadata["served_by"] == "backup" and primary.calls == 2

    rejected = Flaky("main", [RuntimeError("400 INVALID_ARGUMENT bad schema")])
    with pytest.raises(RuntimeError, match="INVALID_ARGUMENT"):
        await ResilientLLM([rejected, backup], 3, 0).invoke([], [])
    assert rejected.calls == 1  # never retried, never sent to the fallback

    down = Flaky("main", [busy] * 5)
    with pytest.raises(LLMUnavailable):
        await ResilientLLM([down], 2, 0).invoke([], [])


async def test_rate_limiter_spaces_calls(monkeypatch):
    from operyxa.llm import base

    clock = {"now": 0.0, "slept": 0.0}

    async def fake_sleep(seconds):
        clock["now"] += seconds
        clock["slept"] += seconds

    monkeypatch.setattr(base.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(base.asyncio, "sleep", fake_sleep)
    limiter = base.RateLimiter(2)
    for _ in range(2):
        await limiter.acquire()
    assert clock["slept"] == 0
    await limiter.acquire()  # the third call of the minute has to wait for the window
    assert 60 <= clock["slept"] < 61


def test_plan_ids_are_normalised_instead_of_rejected():
    raw = {
        "steps": [
            {
                "id": "Analyze-Repository Architecture and all of its many dependency definitions!",
                "agent": "repository",
                "action": "inspect the tree",
            },
            {
                "id": "2nd step",
                "agent": "security",
                "action": "scan dependencies",
                "depends_on": [
                    "Analyze-Repository Architecture and all of its many dependency definitions!"
                ],
            },
        ]
    }
    plan, errors = validate_plan(raw, max_steps=6, allowed_agents=AGENTS)
    assert errors == []
    first, second = plan["steps"]
    assert len(first["id"]) <= 40 and second["id"] == "s_2nd_step"
    assert second["depends_on"] == [first["id"]]


async def test_quota_error_moves_to_fallback_or_waits_on_last_model():
    from langchain_core.messages import AIMessage

    from operyxa.llm.base import ResilientLLM

    class Model:
        def __init__(self, name, errors):
            self.model_name, self.errors, self.calls = name, list(errors), 0

        async def invoke(self, messages, tools, tool_choice=None):
            self.calls += 1
            if self.errors:
                raise self.errors.pop(0)
            return AIMessage(content="ok")

    quota = RuntimeError("429 RESOURCE_EXHAUSTED quota")
    main, backup = Model("main", [quota] * 9), Model("backup", [quota])
    ai = await ResilientLLM([main, backup], 3, 0, quota_wait_seconds=0).invoke([], [])
    assert main.calls == 1  # no retries wasted on a spent quota
    assert backup.calls == 2 and ai.response_metadata["served_by"] == "backup"


def test_model_call_budget_is_shared_between_steps():
    from operyxa.agent.graph import turns_per_step

    assert turns_per_step(20, 2, 8) == 8  # short plan: the per-step cap applies
    assert turns_per_step(20, 4, 8) == 4  # 4 steps x 4 turns + planner + reporter = 18 <= 20
    assert turns_per_step(20, 6, 8) == 3
    assert turns_per_step(6, 4, 8) == 2  # never below the minimum needed to act then conclude


async def test_rejected_request_switches_to_the_backup_provider():
    import pytest
    from langchain_core.messages import AIMessage

    from operyxa.errors import LLMUnavailable
    from operyxa.llm.base import ResilientLLM

    class Model:
        def __init__(self, name, provider, errors):
            self.model_name, self.provider = name, provider
            self.errors, self.calls = list(errors), 0

        async def invoke(self, messages, tools, tool_choice=None):
            self.calls += 1
            if self.errors:
                raise self.errors.pop(0)
            return AIMessage(content="ok")

    unauthorized = RuntimeError("401 Unauthorized: invalid api key")
    nvidia = Model("nemotron", "openai_compatible", [unauthorized])
    nvidia_backup = Model("lightning", "openai_compatible", [unauthorized])
    gemini = Model("gemini-3.8-flash", "gemini", [])
    llm = ResilientLLM([nvidia, nvidia_backup, gemini], 3, 0)
    ai = await llm.invoke([], [])
    assert ai.response_metadata["served_by"] == "gemini-3.8-flash"
    assert nvidia.calls == 1 and nvidia_backup.calls == 0  # same provider, same rejection

    busy = RuntimeError("503 overloaded")
    nvidia, gemini = Model("n", "openai_compatible", [busy] * 3), Model("g", "gemini", [])
    ai = await ResilientLLM([nvidia, gemini], 2, 0).invoke([], [])
    assert ai.response_metadata["served_by"] == "g" and nvidia.calls == 3

    both_down = [Model("n", "openai_compatible", [busy] * 3), Model("g", "gemini", [busy] * 3)]
    with pytest.raises(LLMUnavailable):
        await ResilientLLM(both_down, 2, 0).invoke([], [])

    gemini_rejects = Model("g", "gemini", [unauthorized])
    with pytest.raises(RuntimeError, match="401"):
        await ResilientLLM(
            [Model("n", "openai_compatible", [unauthorized]), gemini_rejects], 2, 0
        ).invoke([], [])


def test_provider_chain_adds_gemini_after_nvidia_when_its_key_is_set():
    from operyxa.config import Settings
    from operyxa.llm.factory import build_llm, model_names, provider_chain

    base = dict(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="nvapi-test",
        llm_model="nvidia/nemotron-3-ultra-550b-a55b",
        llm_fallback_model="nvidia/nemotron-3.5-lightning-30b-a3b",
        gemini_model="gemini-3.8-flash",
        gemini_requests_per_minute=5,
    )
    assert model_names(Settings(**base)) == [
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
    ]
    with_gemini = Settings(**base, gemini_api_key="gemini-test")
    assert provider_chain(with_gemini)[-1] == ("gemini", "gemini-3.8-flash")
    llm = build_llm(with_gemini)
    assert [c.provider for c in llm._clients] == ["openai_compatible"] * 2 + ["gemini"]
    assert [lim._limit for lim in llm._limiters] == [0, 0, 5]
    off = Settings(**base, gemini_api_key="gemini-test", llm_cross_provider_fallback=False)
    assert len(provider_chain(off)) == 2
    reverse = Settings(**{**base, "llm_provider": "gemini"}, gemini_api_key="gemini-test")
    assert provider_chain(reverse)[0] == ("gemini", "gemini-3.8-flash")
    assert provider_chain(reverse)[1][0] == "openai_compatible"


def test_gemini_accepts_tool_calls_made_by_another_provider():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_google_genai import chat_models

    from operyxa.llm.gemini import with_foreign_signatures

    history = [
        HumanMessage("review"),
        AIMessage(
            content="",
            tool_calls=[{"name": "github__get_repo", "args": {}, "id": "chatcmpl-tool-1"}],
        ),
        ToolMessage('{"ok": 1}', tool_call_id="chatcmpl-tool-1"),
    ]
    _, contents = chat_models._parse_chat_history(with_foreign_signatures(history))
    call = contents[1].parts[0]
    assert call.function_call.name == "github__get_repo"
    assert call.thought_signature == b"skip_thought_signature_validator"
    assert contents[2].parts[0].function_response.name == "github__get_repo"
    assert "__gemini_function_call_thought_signatures__" not in history[1].additional_kwargs
