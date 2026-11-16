from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI


class GeminiClient:
    def __init__(self, model: str, api_key: str):
        self.model_name = model
        self._model = ChatGoogleGenerativeAI(
            model=model, google_api_key=api_key, temperature=0, max_retries=2
        )

    async def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str | None = None,
    ) -> AIMessage:
        bound = self._model.bind_tools(tools, tool_choice=tool_choice) if tools else self._model
        return await bound.ainvoke(messages)
