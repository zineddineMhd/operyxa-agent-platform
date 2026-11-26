"""Bounded ReAct loop for one plan step: act -> observe -> decide, until a structured result."""

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from operyxa.agent import prompts
from operyxa.agent.schemas import StepResult, schema_tool, tool_spec
from operyxa.gateway.gateway import CallContext, ToolGateway
from operyxa.llm.base import MeteredLLM

SUBMIT = "submit_step_result"
SUBMIT_TOOL = schema_tool(
    SUBMIT, "Finish this step with a summary and evidence-backed findings.", StepResult
)


@dataclass
class StepInput:
    key: str
    agent: str
    action: str
    objective: str
    dependencies: dict[str, str]  # step id -> summary of an upstream step


async def run_step(
    step: StepInput, *, llm: MeteredLLM, gateway: ToolGateway, ctx: CallContext, max_turns: int
) -> StepResult:
    tool_defs = gateway.tools_for(step.agent, ctx.policy)
    tools = [tool_spec(t.model_name, t.description, t.input_schema) for t in tool_defs]
    tools.append(SUBMIT_TOOL)
    task: dict[str, Any] = {
        "objective": step.objective,
        "your_step": step.action,
        "repository": f"{ctx.scope['owner']}/{ctx.scope['repo']}",
        "commit": ctx.scope["ref"],
    }
    if step.dependencies:
        task["results_of_previous_steps"] = step.dependencies
    messages: list[BaseMessage] = [
        SystemMessage(prompts.AGENTS[step.agent] + prompts.AGENT_RULES),
        HumanMessage(json.dumps(task, ensure_ascii=False)),
    ]

    for turn in range(max_turns):
        last_turn = turn == max_turns - 1
        ai = await llm.invoke(messages, tools, SUBMIT if last_turn else None, step_key=step.key)
        messages.append(ai)
        if not ai.tool_calls:
            messages.append(HumanMessage(f"Call `{SUBMIT}` now to finish the step."))
            continue
        for call in ai.tool_calls:
            if call["name"] == SUBMIT:
                try:
                    return StepResult.model_validate(call["args"])
                except ValidationError as exc:
                    if last_turn:
                        raise
                    messages.append(
                        ToolMessage(
                            f"Invalid result, fix and resubmit: {exc.errors(include_url=False)[:5]}",
                            tool_call_id=call["id"],
                        )
                    )
                    continue
            outcome = await gateway.call(
                ctx, gateway.resolve_model_name(call["name"]), call["args"]
            )
            messages.append(ToolMessage(outcome.for_model(), tool_call_id=call["id"]))
    raise RuntimeError("the agent did not submit a result within its turn limit")
