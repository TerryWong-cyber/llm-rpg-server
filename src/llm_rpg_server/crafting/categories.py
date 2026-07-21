from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from llm_rpg_server.shared.config import ContentProvider

from .models import CraftProperties, CraftPropertyProposal, ItemType, PropertyValue

PropertyKind = Literal["integer", "number", "boolean", "enum"]


class PropertyRule(BaseModel):
    kind: PropertyKind
    minimum: float | None = None
    maximum: float | None = None
    choices: list[str] = Field(default_factory=list)
    default: PropertyValue

    @model_validator(mode="after")
    def validate_shape(self) -> PropertyRule:
        if self.kind == "enum" and not self.choices:
            raise ValueError("Enum property rules require choices")
        if self.kind != "enum" and self.choices:
            raise ValueError("Only enum property rules may declare choices")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Property minimum cannot exceed maximum")
        return self


class CraftCategoryDefinition(BaseModel):
    item_type: ItemType
    prompt_id: str
    use_contexts: list[Literal["combat", "exploration", "world_event"]] = Field(default_factory=list)
    property_rules: dict[str, PropertyRule] = Field(default_factory=dict)


class CraftCategoryDocument(BaseModel):
    schema_version: str
    categories: dict[str, CraftCategoryDefinition]


class CraftCategoryCatalog:
    """Validated, configuration-backed category and numeric-rule registry."""

    def __init__(self, content: ContentProvider):
        self.document = CraftCategoryDocument.model_validate(content.document("crafting/categories.json"))

    def categories_for(self, item_type: ItemType) -> list[str]:
        return sorted(
            category_id
            for category_id, definition in self.document.categories.items()
            if definition.item_type == item_type
        )

    def definition(self, category: str) -> CraftCategoryDefinition:
        try:
            return self.document.categories[category]
        except KeyError as exc:
            raise ValueError(f"Unknown crafting category: {category}") from exc

    def validate_category(self, category: str, item_type: ItemType) -> CraftCategoryDefinition:
        definition = self.definition(category)
        if definition.item_type != item_type:
            raise ValueError(
                f"Crafting category {category!r} is not valid for resulting item type {item_type!r}"
            )
        return definition

    def property_contract(self, category: str) -> str:
        definition = self.definition(category)
        payload = {
            field: rule.model_dump(mode="json")
            for field, rule in definition.property_rules.items()
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def normalize_proposal(self, category: str, proposal: CraftPropertyProposal) -> CraftProperties:
        definition = self.definition(category)
        properties = {
            field: self._normalize_value(rule, proposal.properties.get(field, rule.default))
            for field, rule in definition.property_rules.items()
        }
        tags = sorted({tag.strip().lower() for tag in proposal.tags if tag.strip()})[:8]
        return CraftProperties(
            category=category,
            item_type=definition.item_type,
            can_be_ingredient=proposal.can_be_ingredient,
            tradable=proposal.tradable,
            use_contexts=definition.use_contexts,
            tags=tags,
            properties=properties,
        )

    @staticmethod
    def _normalize_value(rule: PropertyRule, value: Any) -> PropertyValue:
        if rule.kind == "enum":
            return value if isinstance(value, str) and value in rule.choices else rule.default
        if rule.kind == "boolean":
            return value if isinstance(value, bool) else rule.default
        if isinstance(value, bool):
            value = rule.default
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = float(rule.default)
        if rule.minimum is not None:
            numeric = max(rule.minimum, numeric)
        if rule.maximum is not None:
            numeric = min(rule.maximum, numeric)
        return int(round(numeric)) if rule.kind == "integer" else numeric
