"""Collect provider-reported token usage from the AWorld OpenAI adapter."""

from contextlib import contextmanager

from utils.llm_usage import usage_from_result


@contextmanager
def capture_aworld_usage():
    # AWorld calls this module-level function after each provider completion,
    # but its Runners.run result may not expose the same usage to our agents.
    import aworld.models.openai_provider as provider_module

    original = provider_module.usage_process
    calls = []

    def capture(usage, *args, **kwargs):
        normalized = usage_from_result(type("UsageResult", (), {"usage": usage})())
        if normalized["total_tokens"]:
            calls.append(normalized)
        return original(usage, *args, **kwargs)

    provider_module.usage_process = capture
    try:
        yield calls
    finally:
        provider_module.usage_process = original
