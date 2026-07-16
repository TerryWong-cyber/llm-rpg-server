from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_content_root() -> Path:
    repository_content = project_root() / "configs"
    if repository_content.is_dir():
        return repository_content
    return Path(__file__).resolve().parents[2] / "configs"


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    content_root: Path
    openai_api_key: str
    openai_base_url: str | None
    llm_model: str
    llm_temperature: float
    image_model: str
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        root = project_root()
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
        origins = tuple(filter(None, (item.strip() for item in os.getenv("CORS_ORIGINS", "*").split(","))))
        return cls(
            project_root=root,
            content_root=Path(os.getenv("CONTENT_ROOT", default_content_root())).resolve(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "not-configured"),
            openai_base_url=base_url,
            llm_model=os.getenv("LLM_MODEL", "qwen3-vl-8b-instruct"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.5")),
            image_model=os.getenv("IMAGE_MODEL", "dall-e-3"),
            cors_origins=origins or ("*",),
        )
