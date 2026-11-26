import base64
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI

# Gemini 3 rejects a past function call without a thought signature. Calls made by another
# provider (after a fallback in the middle of a run) have none: Google documents this value
# for history that did not come from Gemini.
_FOREIGN_SIGNATURE = base64.b64encode(b"skip_thought_signature_validator").decode()
_SIGNATURES_KEY = "__gemini_function_call_thought_signatures__"


def with_foreign_signatures(messages: list[BaseMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            signatures = dict(message.additional_kwargs.get(_SIGNATURES_KEY, {}))
            missing = [c["id"] for c in message.tool_calls if c.get("id") not in signatures]
            if missing:
                signatures.update({call_id: _FOREIGN_SIGNATURE for call_id in missing})
                kwargs = {**message.additional_kwargs, _SIGNATURES_KEY: signatures}
                message = message.model_copy(update={"additional_kwargs": kwargs})
        out.append(message)
    return out


class GeminiClient:
    provider = "gemini"

    def __init__(self, model: str, api_key: str):
        self.model_name = model
        self._model = ChatGoogleGenerativeAI(
            model=model, google_api_key=api_key, temperature=0, max_retries=0
        )

    async def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
    ) -> AIMessage:
        bound = self._model.bind_tools(tools, tool_choice=tool_choice) if tools else self._model
        return await bound.ainvoke(with_foreign_signatures(messages))
