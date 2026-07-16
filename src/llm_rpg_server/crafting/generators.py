from __future__ import annotations

from typing import Any, Protocol

from llm_rpg_server.shared.config import ContentProvider, Settings

from .models import CraftNarrative


class CraftNarrativeGenerator(Protocol):
    def generate(self, first: dict[str, Any], second: dict[str, Any]) -> CraftNarrative: ...


class ItemImageGenerator(Protocol):
    def generate(self, name: str, description: str) -> str: ...


class LLMCraftNarrativeGenerator:
    def __init__(self, content: ContentProvider, llm: Any):
        self.content = content
        self.llm = llm

    def generate(self, first: dict[str, Any], second: dict[str, Any]) -> CraftNarrative:
        from langchain_core.prompts import ChatPromptTemplate

        definition = self.content.prompt("crafting")
        prompt = ChatPromptTemplate.from_messages([("system", definition.system), ("human", definition.user)])
        chain = prompt | self.llm.with_structured_output(CraftNarrative)
        return chain.invoke({
            "item1_name": first["name"],
            "item1_description": first.get("desc", ""),
            "item1_value": first.get("value", 0),
            "item2_name": second["name"],
            "item2_description": second.get("desc", ""),
            "item2_value": second.get("value", 0),
        })


class OpenAIItemImageGenerator:
    def __init__(self, content: ContentProvider, settings: Settings):
        self.content = content
        self.settings = settings

    def generate(self, name: str, description: str) -> str:
        prompt = self.content.text("crafting.image_prompt", name=name, description=description)
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.settings.openai_api_key, base_url=self.settings.openai_base_url)
            response = client.images.generate(
                model=self.settings.image_model,
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )
            return response.data[0].url or ""
        except Exception:
            return self.content.text("crafting.placeholder_url", name=name)

