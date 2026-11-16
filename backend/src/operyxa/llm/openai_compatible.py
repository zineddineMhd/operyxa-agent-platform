"""Any OpenAI-style chat API with tool calling: NVIDIA NIM, OpenRouter, a local server..."""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI


class OpenAICompatibleClient:
    def __init__(self, model: str, base_url: str, api_key: str):
        self.model_name = model
        self._model = ChatOpenAI(
            model=model, base_url=base_url, api_key=api_key, temperature=0, max_retries=0
        )

    async def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
    ) -> AIMessage:
        bound = self._model.bind_tools(tools, tool_choice=tool_choice) if tools else self._model
        return await bound.ainvoke(messages)
