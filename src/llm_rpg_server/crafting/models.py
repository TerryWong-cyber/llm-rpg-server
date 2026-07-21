from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ItemType = Literal["weapon", "armor", "item", "material"]
ImageStatus = Literal["generated", "fallback"]
PropertyValue = str | int | float | bool


class ItemReference(BaseModel):
    item_type: ItemType
    item_id: str

    @property
    def identity(self) -> str:
        return f"{self.item_type}:{self.item_id}"


class CraftResult(BaseModel):
    id: str = ""
    name: str
    desc: str
    value: int = Field(ge=0)
    item_type: ItemType
    combat_stat: int = Field(ge=0)
    image_url: str = ""
    image_key: str = ""
    image_status: ImageStatus | None = None
    can_be_ingredient: bool
    tradable: bool = True
    use_contexts: list[Literal["combat", "exploration", "world_event"]] = Field(default_factory=list)
    category: str = "misc"
    tags: list[str] = Field(default_factory=list)
    properties: dict[str, PropertyValue] = Field(default_factory=dict)
    ingredient_ancestry: list[ItemReference] = Field(default_factory=list, exclude=True)

    def public_dict(self) -> dict:
        payload = self.model_dump(mode="json", exclude={"ingredient_ancestry"})
        payload["type"] = self.item_type
        return payload


class CraftConcept(BaseModel):
    """The first graph node's content-only result."""

    success: bool
    reason: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=40)
    desc: str = Field(default="", max_length=50)

    category: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def validate_success_payload(self) -> CraftConcept:
        if self.success and (not self.name.strip() or not self.desc.strip() or not self.category.strip()):
            raise ValueError("Successful crafting concepts require a name, description, and category")
        return self


class CraftDecision(CraftConcept):
    """Compatibility model for callers that still import CraftDecision.

    New code must use :class:`CraftConcept` for the first graph node and
    :class:`CraftPropertyProposal` for the third node.
    """

    can_be_ingredient: bool = False
    tradable: bool = True
    use_contexts: list[Literal["combat", "exploration", "world_event"]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=8)


class CraftPropertyProposal(BaseModel):
    """A category-specific property proposal, validated against config later."""

    can_be_ingredient: bool = True
    tradable: bool = True
    tags: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def unwrap_properties_envelope(cls, value: Any) -> Any:
        """Accept an extra envelope emitted by some OpenAI-compatible models."""
        if not isinstance(value, dict):
            return value
        wrapped = value.get("properties")
        if not isinstance(wrapped, dict) or not any(
            field in wrapped for field in ("can_be_ingredient", "tradable", "tags", "properties")
        ):
            return value
        proposal = dict(wrapped)
        for field in ("can_be_ingredient", "tradable", "tags"):
            if field in value and field not in proposal:
                proposal[field] = value[field]
        return proposal


class CraftProperties(BaseModel):
    category: str
    item_type: ItemType
    can_be_ingredient: bool
    tradable: bool
    use_contexts: list[Literal["combat", "exploration", "world_event"]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=8)
    properties: dict[str, PropertyValue] = Field(default_factory=dict)


class CraftArtwork(BaseModel):
    image_url: str = ""
    image_key: str = Field(min_length=1)
    status: ImageStatus


class CraftDraft(BaseModel):
    concept: CraftConcept
    properties: CraftProperties
    artwork: CraftArtwork


class CraftAttempt(BaseModel):
    success: bool
    result: CraftResult | None = None
    failure_reason: str = ""
    duration_ms: int = Field(default=0, ge=0)
    recipe_cached: bool = False


class RecipeRecord(BaseModel):
    ingredients: tuple[ItemReference, ItemReference]
    success: bool
    result: CraftResult | None = None
    failure_reason: str = ""
    duration_ms: int = Field(default=0, ge=0)

    def public_dict(self) -> dict:
        first, second = self.ingredients
        return {
            "ingredients": [first.model_dump(mode="json"), second.model_dump(mode="json")],
            "mat1_id": first.item_id,
            "mat2_id": second.item_id,
            "success": self.success,
            "result": self.result.public_dict() if self.result else None,
            "failure_reason": self.failure_reason,
            "duration_ms": self.duration_ms,
        }
