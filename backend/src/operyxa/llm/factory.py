from operyxa.config import Settings
from operyxa.llm.base import LLMClient


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("LLM_PROVIDER=gemini requires GEMINI_API_KEY")
        from operyxa.llm.gemini import GeminiClient

        return GeminiClient(settings.gemini_model, settings.gemini_api_key)
    from operyxa.llm.demo import DemoLLM

    return DemoLLM()
