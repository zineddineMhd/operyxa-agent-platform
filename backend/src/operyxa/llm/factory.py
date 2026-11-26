from operyxa.config import Settings
from operyxa.llm.base import LLMClient, ResilientLLM

PROVIDERS = ("gemini", "openai_compatible")


def _provider_models(settings: Settings, provider: str) -> list[str]:
    if provider == "gemini":
        names = [settings.gemini_model, settings.gemini_fallback_model]
        configured = bool(settings.gemini_api_key)
    else:
        names = [settings.llm_model, settings.llm_fallback_model]
        configured = bool(settings.llm_api_key)
    return [n for n in names if n] if configured else []


def provider_chain(settings: Settings) -> list[tuple[str, str]]:
    """(provider, model) in the order they are tried: main provider, then the backup one."""
    if settings.llm_provider == "demo":
        return [("demo", "demo-deterministic")]
    main = settings.llm_provider
    chain = [(main, name) for name in _main_models(settings)]
    if settings.llm_cross_provider_fallback:
        for other in PROVIDERS:
            if other != main:
                chain += [(other, name) for name in _provider_models(settings, other)]
    return chain


def _main_models(settings: Settings) -> list[str]:
    if settings.llm_provider == "gemini":
        names = [settings.gemini_model, settings.gemini_fallback_model]
    else:
        names = [settings.llm_model, settings.llm_fallback_model]
    return [n for n in names if n]


def model_names(settings: Settings) -> list[str]:
    """Main model first, then the fallbacks."""
    return [name for _, name in provider_chain(settings)]


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "demo":
        from operyxa.llm.demo import DemoLLM

        return DemoLLM()
    if settings.llm_provider == "gemini" and not settings.gemini_api_key:
        raise RuntimeError("LLM_PROVIDER=gemini requires GEMINI_API_KEY")
    if settings.llm_provider == "openai_compatible" and not (
        settings.llm_api_key and settings.llm_model
    ):
        raise RuntimeError("LLM_PROVIDER=openai_compatible requires LLM_API_KEY and LLM_MODEL")
    clients: list[LLMClient] = []
    limits: list[int] = []
    for provider, name in provider_chain(settings):
        if provider == "gemini":
            from operyxa.llm.gemini import GeminiClient

            clients.append(GeminiClient(name, settings.gemini_api_key))
            limits.append(settings.gemini_requests_per_minute or settings.llm_requests_per_minute)
        else:
            from operyxa.llm.openai_compatible import OpenAICompatibleClient

            clients.append(
                OpenAICompatibleClient(name, settings.llm_base_url, settings.llm_api_key)
            )
            limits.append(settings.llm_requests_per_minute)
    return ResilientLLM(
        clients,
        settings.llm_max_retries,
        settings.llm_retry_base_seconds,
        settings.llm_timeout_seconds,
        limits,
    )
