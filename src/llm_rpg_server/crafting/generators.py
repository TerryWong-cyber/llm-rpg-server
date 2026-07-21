from __future__ import annotations

import json
from typing import Any, Protocol

from llm_rpg_server.shared.config import ContentProvider

from .categories import CraftCategoryCatalog
from .models import CraftConcept, CraftDecision, CraftPropertyProposal, ItemType


class CraftConceptGenerator(Protocol):
    def generate(
        self,
        first: dict[str, Any],
        second: dict[str, Any],
        result_type: ItemType,
        allowed_categories: list[str],
    ) -> CraftConcept: ...


class CraftPropertyGenerator(Protocol):
    def generate(
        self,
        first: dict[str, Any],
        second: dict[str, Any],
        concept: CraftConcept,
        property_contract: str,
        prompt_id: str,
    ) -> CraftPropertyProposal: ...


class LLMCraftConceptGenerator:
    """The content-only first node. It cannot set game mechanics or assets."""

    def __init__(self, content: ContentProvider, llm: Any):
        self.content = content
        self.llm = llm

    def generate(
        self,
        first: dict[str, Any],
        second: dict[str, Any],
        result_type: ItemType,
        allowed_categories: list[str],
    ) -> CraftConcept:
        from langchain_core.prompts import ChatPromptTemplate

        definition = self.content.prompt("crafting_concept")
        prompt = ChatPromptTemplate.from_messages([("system", definition.system), ("human", definition.user)])
        chain = prompt | self.llm.with_structured_output(CraftConcept)
        return chain.invoke({
            "item1_type": first["_crafting_type"],
            "item1_name": first["name"],
            "item1_description": first.get("desc", ""),
            "item2_type": second["_crafting_type"],
            "item2_name": second["name"],
            "item2_description": second.get("desc", ""),
            "result_type": result_type,
            "allowed_categories": ", ".join(allowed_categories),
        })


class LLMCraftPropertyGenerator:
    """The third node. It proposes category fields that config clamps afterwards."""

    def __init__(self, content: ContentProvider, categories: CraftCategoryCatalog, llm: Any):
        self.content = content
        self.categories = categories
        self.llm = llm

    def generate(
        self,
        first: dict[str, Any],
        second: dict[str, Any],
        concept: CraftConcept,
        property_contract: str,
        prompt_id: str,
    ) -> CraftPropertyProposal:
        from langchain_core.prompts import ChatPromptTemplate

        definition = self.content.prompt(prompt_id)
        prompt = ChatPromptTemplate.from_messages([("system", definition.system), ("human", definition.user)])
        chain = prompt | self.llm.with_structured_output(CraftPropertyProposal)
        return chain.invoke({
            "name": concept.name,
            "description": concept.desc,
            "category": concept.category,
            "ingredients": json.dumps([_ingredient_view(first), _ingredient_view(second)], ensure_ascii=False),
            "property_contract": property_contract,
        })


class LLMCraftDecisionGenerator(LLMCraftConceptGenerator):
    """Compatibility facade for integrations still importing the previous class."""

    def evaluate(self, first: dict[str, Any], second: dict[str, Any], result_type: str) -> CraftDecision:
        categories = [result_type]
        concept = self.generate(first, second, result_type, categories)  # type: ignore[arg-type]
        return CraftDecision.model_validate(concept.model_dump())


def _ingredient_view(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": definition.get("_crafting_type", "material"),
        "name": definition.get("name", ""),
        "description": definition.get("desc", ""),
        "category": definition.get("category", ""),
        "tags": list(definition.get("tags", [])),
    }
