"""Deterministic offline model.

It follows the same protocol as the real model (plan -> tool calls -> structured results) with
fixed heuristics instead of reasoning. Used for CI, local development without an API key, and
frontend work. Its findings come only from the static-analysis tool output.
"""

import json
import re
import uuid
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

_RESULT = re.compile(
    r'<tool_result evidence_id="(ev_\w+)" tool="([\w.]+)"[^>]*>\n(.*)\n</tool_result>', re.S
)
_CATEGORY = {
    "unpinned-dependency": "dependencies",
    "known-vulnerability": "dependencies",
    "unsafe-dependency-spec": "dependencies",
    "missing-lockfile": "dependencies",
    "invalid-manifest": "dependencies",
    "no-tests": "testing",
    "no-ci": "maintainability",
}
_ADVICE = {
    "unpinned-dependency": "Pin exact versions or commit a lockfile.",
    "known-vulnerability": "Upgrade to a fixed version listed by the advisory.",
    "missing-lockfile": "Generate and commit a lockfile so builds are reproducible.",
    "env-file-committed": "Remove the file from git, rotate any secret it held, add it to .gitignore.",
    "docker-runs-as-root": "Add a non-root USER instruction.",
    "docker-unpinned-base-image": "Pin the base image to a version tag or digest.",
    "workflow-pull-request-target": "Use pull_request, or never check out untrusted code here.",
    "workflow-action-on-branch": "Pin the action to a release tag or a commit SHA.",
    "no-ci": "Add a CI pipeline that runs linting and tests on every pull request.",
    "no-tests": "Add automated tests for the critical paths.",
}


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": "call_" + uuid.uuid4().hex[:12]}


class DemoLLM:
    model_name = "demo-deterministic"

    async def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
    ) -> AIMessage:
        names = {t["function"]["name"] for t in tools}
        if "submit_plan" in names:
            calls = [
                _call(
                    "submit_plan",
                    {
                        "steps": self._testing_step(messages)
                        + [
                            {
                                "id": "repo_overview",
                                "agent": "repository",
                                "depends_on": [],
                                "action": "Map the repository structure and review the five latest commits.",
                            },
                            {
                                "id": "security_review",
                                "agent": "security",
                                "depends_on": [],
                                "action": "Scan dependency manifests and configuration for weaknesses.",
                            },
                        ]
                    },
                )
            ]
        elif "submit_report" in names:
            calls = [_call("submit_report", self._report(str(messages[-1].content)))]
        else:
            results = [
                m.groups()
                for msg in messages
                if isinstance(msg, ToolMessage)
                if (m := _RESULT.search(str(msg.content)))
            ]
            seen_tool_output = any(isinstance(m, ToolMessage) for m in messages)
            if "test_runner__run_test_profile" in names and tool_choice != "submit_step_result":
                calls = self._test(results, seen_tool_output, messages)
            elif not seen_tool_output and tool_choice != "submit_step_result":
                calls = self._explore(names)
            else:
                calls = []
            if not calls:
                calls = [_call("submit_step_result", self._step_result(results))]
        usage = {
            "input_tokens": sum(len(str(m.content)) for m in messages) // 4,
            "output_tokens": len(json.dumps(calls)) // 4,
        }
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        return AIMessage(content="", tool_calls=calls, usage_metadata=usage)

    @staticmethod
    def _testing_step(messages: list[BaseMessage]) -> list[dict[str, Any]]:
        try:
            request = json.loads(str(messages[-1].content))
        except ValueError:
            return []
        if "testing" not in request.get("available_agents", []):
            return []
        return [
            {
                "id": "test_suite",
                "agent": "testing",
                "depends_on": [],
                "action": "Run the repository's test suite with the applicable test profile.",
            }
        ]

    @staticmethod
    def _test(
        results: list[tuple[str, str, str]], seen_tool_output: bool, messages: list[BaseMessage]
    ) -> list[dict[str, Any]]:
        """list the profiles, run the first applicable one, then conclude."""
        if not seen_tool_output:
            return [_call("test_runner__list_test_profiles", {})]
        tools = {tool for _, tool, _ in results}
        failed = any(
            "<tool_error" in str(m.content) for m in messages if isinstance(m, ToolMessage)
        )
        if tools != {"test_runner.list_test_profiles"} or failed:
            return []
        body = next(b for _, t, b in results if t == "test_runner.list_test_profiles")
        try:
            profiles = [p["name"] for p in json.loads(body)["profiles"] if p["applicable"]]
        except (ValueError, KeyError, TypeError):
            return []
        return (
            [_call("test_runner__run_test_profile", {"profile": profiles[0]})] if profiles else []
        )

    @staticmethod
    def _explore(names: set[str]) -> list[dict[str, Any]]:
        if "analysis__scan_dependencies" in names:
            wanted = ("scan_dependencies", "scan_vulnerabilities", "check_config")
            return [_call(f"analysis__{t}", {}) for t in wanted if f"analysis__{t}" in names]
        return [
            _call("github__list_tree", {}),
            _call("github__list_commits", {"limit": 5}),
            _call("github__get_file", {"path": "README.md"}),
        ]

    @staticmethod
    def _step_result(results: list[tuple[str, str, str]]) -> dict[str, Any]:
        findings, notes = [], []
        for evidence_id, tool, body in results:
            try:
                data = json.loads(body)
            except ValueError:
                continue
            if tool == "github.list_tree":
                notes.append(f"{data.get('total', 0)} entries in the tree")
            if tool == "github.list_commits" and data.get("commits"):
                notes.append(f"{len(data['commits'])} recent commits reviewed")
            if tool == "test_runner.run_test_profile":
                counts = ", ".join(f"{n} {k}" for k, n in data.get("counts", {}).items())
                notes.append(f"profile {data.get('profile')}: {data.get('status')} ({counts})")
                for test in data.get("failed_tests", [])[:10]:
                    findings.append(
                        {
                            "title": f"Failing test: {test['test']}"[:200],
                            "severity": "medium",
                            "category": "testing",
                            "description": test.get("reason") or "The test fails on this commit.",
                            "recommendation": "Fix the code or the test before merging.",
                            "evidence": [
                                {"evidence_id": evidence_id, "quote": test["report_line"]}
                            ],
                        }
                    )
            grouped: dict[tuple[str, str], list[dict]] = {}
            for item in data.get("issues", []):
                grouped.setdefault((item["rule"], item["path"]), []).append(item)
            for (rule, path), items in grouped.items():
                first = items[0]
                title = (
                    first["message"]
                    if len(items) == 1
                    else (f"{len(items)} occurrences of {rule.replace('-', ' ')} in {path}")
                )
                findings.append(
                    {
                        "title": title[:200],
                        "severity": first["severity_hint"],
                        "category": _CATEGORY.get(rule, "security"),
                        "description": "; ".join(i["message"] for i in items[:5]),
                        "recommendation": _ADVICE.get(rule, ""),
                        "evidence": [
                            {"evidence_id": evidence_id, "quote": i.get("snippet") or i["message"]}
                            for i in items[:3]
                        ],
                    }
                )
        summary = "; ".join(notes) or f"{len(findings)} issue group(s) identified"
        return {"summary": summary, "findings": findings[:15]}

    @staticmethod
    def _report(payload: str) -> dict[str, Any]:
        try:
            findings = json.loads(payload).get("verified_findings", [])
        except ValueError:
            findings = []
        counts: dict[str, int] = {}
        for item in findings:
            counts[item["severity"]] = counts.get(item["severity"], 0) + 1
        detail = ", ".join(f"{n} {sev}" for sev, n in counts.items()) or "none"
        recommendations = list(
            dict.fromkeys(f["recommendation"] for f in findings if f.get("recommendation"))
        )[:8]
        return {
            "summary": f"{len(findings)} verified finding(s) ({detail}).",
            "recommendations": recommendations,
        }
