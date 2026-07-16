from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from string import Formatter
from threading import RLock
from typing import Any, Protocol

from .models import ContentDocument, PromptDefinition


class ContentError(RuntimeError):
    pass


class ContentProvider(Protocol):
    def document(self, relative_path: str) -> dict[str, Any]: ...

    def prompt(self, prompt_id: str) -> PromptDefinition: ...

    def text(self, text_id: str, **values: Any) -> str: ...


class LocalContentProvider:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def document(self, relative_path: str) -> dict[str, Any]:
        normalized = str(Path(relative_path))
        with self._lock:
            if normalized not in self._cache:
                self._cache[normalized] = self._load(normalized)
            return deepcopy(self._cache[normalized])

    def prompt(self, prompt_id: str) -> PromptDefinition:
        payload = self.document(f"prompts/{prompt_id}.json")
        return PromptDefinition.model_validate(payload)

    def text(self, text_id: str, **values: Any) -> str:
        payload = self.document("narratives/zh-CN.json")
        current: Any = payload["texts"]
        try:
            for part in text_id.split("."):
                current = current[part]
        except (KeyError, TypeError) as exc:
            raise ContentError(f"Unknown text id: {text_id}") from exc
        if not isinstance(current, str):
            raise ContentError(f"Text id does not resolve to a string: {text_id}")
        try:
            return current.format(**values)
        except KeyError as exc:
            raise ContentError(f"Missing template value {exc.args[0]!r} for {text_id}") from exc

    def validate(self) -> None:
        for path in self.root.rglob("*.json"):
            relative = str(path.relative_to(self.root))
            payload = self.document(relative)
            if relative.startswith("prompts/"):
                prompt = PromptDefinition.model_validate(payload)
                self._validate_template(prompt.system, relative)
                self._validate_template(prompt.user, relative)
            elif "schema_version" in payload:
                ContentDocument.model_validate(payload)

    def _load(self, relative_path: str) -> dict[str, Any]:
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents:
            raise ContentError(f"Content path escapes root: {relative_path}")
        try:
            with path.open(encoding="utf-8") as stream:
                payload = json.load(stream)
        except FileNotFoundError as exc:
            raise ContentError(f"Missing content file: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ContentError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ContentError(f"Content root must be an object: {path}")
        return payload

    @staticmethod
    def _validate_template(template: str, source: str) -> None:
        try:
            list(Formatter().parse(template))
        except ValueError as exc:
            raise ContentError(f"Invalid template in {source}: {exc}") from exc
