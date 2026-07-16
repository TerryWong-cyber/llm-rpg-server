from __future__ import annotations

from typing import Any

from .config import Settings


def create_llm(settings: Settings, *, temperature: float | None = None) -> Any:
    from langchain_openai import ChatOpenAI

    options: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "api_key": settings.openai_api_key,
    }
    if settings.openai_base_url:
        options["base_url"] = settings.openai_base_url
    return ChatOpenAI(**options)

