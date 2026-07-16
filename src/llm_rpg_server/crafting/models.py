from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ItemType = Literal["weapon", "armor", "item", "material"]


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
    can_be_ingredient: bool
    ingredient_ancestry: list[ItemReference] = Field(default_factory=list, exclude=True)

    def public_dict(self) -> dict:
        payload = self.model_dump(mode="json", exclude={"ingredient_ancestry"})
        payload["type"] = self.item_type
        return payload


class CraftDecision(BaseModel):
    success: bool
    reason: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=40)
    desc: str = Field(default="", max_length=50)
    can_be_ingredient: bool = False

    @model_validator(mode="after")
    def validate_success_payload(self) -> CraftDecision:
        if self.success and (not self.name.strip() or not self.desc.strip()):
            raise ValueError("Successful crafting decisions require a name and description")
        return self


class CraftAttempt(BaseModel):
    success: bool
    result: CraftResult | None = None
    failure_reason: str = ""


class RecipeRecord(BaseModel):
    ingredients: tuple[ItemReference, ItemReference]
    success: bool
    result: CraftResult | None = None
    failure_reason: str = ""

    def public_dict(self) -> dict:
        first, second = self.ingredients
        return {
            "ingredients": [first.model_dump(mode="json"), second.model_dump(mode="json")],
            "mat1_id": first.item_id,
            "mat2_id": second.item_id,
            "success": self.success,
            "result": self.result.public_dict() if self.result else None,
            "failure_reason": self.failure_reason,
        }
