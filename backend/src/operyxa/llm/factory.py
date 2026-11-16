from operyxa.config import Settings
from operyxa.llm.base import LLMClient, ResilientLLM


def model_names(settings: Settings) -> list[str]:
    """Main model first, then the optional fallback."""
    if settings.llm_provider == "gemini":
        names = [settings.gemini_model, settings.gemini_fallback_model]
    elif settings.llm_provider == "openai_compatible":
        names = [settings.llm_model, settings.llm_fallback_model]
    else:
        names = ["demo-deterministic"]
    return [n for n in names if n]


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "demo":
        from operyxa.llm.demo import DemoLLM

        return DemoLLM()
    names = model_names(settings)
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("LLM_PROVIDER=gemini requires GEMINI_API_KEY")
        from operyxa.llm.gemini import GeminiClient

        clients: list[LLMClient] = [GeminiClient(n, settings.gemini_api_key) for n in names]
    else:
        if not (settings.llm_api_key and names):
            raise RuntimeError("LLM_PROVIDER=openai_compatible requires LLM_API_KEY and LLM_MODEL")
        from operyxa.llm.openai_compatible import OpenAICompatibleClient

        clients = [
            OpenAICompatibleClient(n, settings.llm_base_url, settings.llm_api_key) for n in names
        ]
    return ResilientLLM(
        clients,
        settings.llm_max_retries,
        settings.llm_retry_base_seconds,
        settings.llm_timeout_seconds,
        settings.llm_requests_per_minute,
    )
