from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from .artwork import CraftArtworkGenerator
from .categories import CraftCategoryCatalog
from .generators import CraftConceptGenerator, CraftPropertyGenerator
from .models import CraftConcept, CraftDraft, CraftProperties, CraftArtwork, ItemType


class CraftWorkflowState(TypedDict, total=False):
    first: dict[str, Any]
    second: dict[str, Any]
    result_type: ItemType
    allowed_categories: list[str]
    concept: CraftConcept
    artwork: CraftArtwork
    properties: CraftProperties
    draft: CraftDraft
    failure_reason: str


class CraftWorkflowOutcome(BaseModel):
    success: bool
    draft: CraftDraft | None = None
    failure_reason: str = ""


class CraftingWorkflow:
    """A LangGraph orchestration layer; every node owns exactly one concern."""

    def __init__(
        self,
        categories: CraftCategoryCatalog,
        concept_generator: CraftConceptGenerator,
        artwork_generator: CraftArtworkGenerator,
        property_generator: CraftPropertyGenerator,
    ):
        self.categories = categories
        self.concept_generator = concept_generator
        self.artwork_generator = artwork_generator
        self.property_generator = property_generator
        self.app = self._compile()

    def run(
        self,
        first: dict[str, Any],
        second: dict[str, Any],
        result_type: ItemType,
    ) -> CraftWorkflowOutcome:
        allowed_categories = self.categories.categories_for(result_type)
        if not allowed_categories:
            raise RuntimeError(f"No crafting category is configured for item type {result_type!r}")
        state = self.app.invoke({
            "first": first,
            "second": second,
            "result_type": result_type,
            "allowed_categories": allowed_categories,
        })
        concept = state.get("concept")
        if concept is not None and not concept.success:
            return CraftWorkflowOutcome(success=False, failure_reason=concept.reason.strip())
        draft = state.get("draft")
        if draft is None:
            raise RuntimeError("Crafting workflow completed without a crafted item")
        return CraftWorkflowOutcome(success=True, draft=draft)

    def _compile(self):
        graph = StateGraph(CraftWorkflowState)
        graph.add_node("Concept", self._concept)
        graph.add_node("FanOut", lambda state: {})
        graph.add_node("Artwork", self._artwork)
        graph.add_node("Properties", self._properties)
        graph.add_node("Assemble", self._assemble)
        graph.add_node("Failed", lambda state: {})
        graph.set_entry_point("Concept")
        graph.add_conditional_edges(
            "Concept",
            self._route_after_concept,
            {"continue": "FanOut", "failed": "Failed"},
        )
        graph.add_edge("FanOut", "Artwork")
        graph.add_edge("FanOut", "Properties")
        graph.add_edge(["Artwork", "Properties"], "Assemble")
        graph.add_edge("Assemble", END)
        graph.add_edge("Failed", END)
        return graph.compile()

    def _concept(self, state: CraftWorkflowState) -> dict[str, Any]:
        concept = self.concept_generator.generate(
            state["first"],
            state["second"],
            state["result_type"],
            state["allowed_categories"],
        )
        if concept.success:
            self.categories.validate_category(concept.category.strip().lower(), state["result_type"])
            concept = concept.model_copy(update={"category": concept.category.strip().lower()})
        return {"concept": concept}

    @staticmethod
    def _route_after_concept(state: CraftWorkflowState) -> Literal["continue", "failed"]:
        concept = state["concept"]
        return "continue" if concept.success else "failed"

    def _artwork(self, state: CraftWorkflowState) -> dict[str, Any]:
        return {
            "artwork": self.artwork_generator.generate(
                state["first"],
                state["second"],
                state["concept"],
            )
        }

    def _properties(self, state: CraftWorkflowState) -> dict[str, Any]:
        concept = state["concept"]
        definition = self.categories.definition(concept.category)
        proposal = self.property_generator.generate(
            state["first"],
            state["second"],
            concept,
            self.categories.property_contract(concept.category),
            definition.prompt_id,
        )
        return {"properties": self.categories.normalize_proposal(concept.category, proposal)}

    @staticmethod
    def _assemble(state: CraftWorkflowState) -> dict[str, Any]:
        return {
            "draft": CraftDraft(
                concept=state["concept"],
                properties=state["properties"],
                artwork=state["artwork"],
            )
        }
