from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ItemType = Literal["weapon", "armor", "item", "material"]


class ItemReference(BaseModel):
    item_type: ItemType
    item_id: str


class CraftResult(BaseModel):
    id: str = ""
    name: str
    desc: str
    value: int = Field(ge=0)
    item_type: ItemType
    combat_stat: int = Field(ge=0)
    image_url: str = ""

    def public_dict(self) -> dict:
        payload = self.model_dump(mode="json")
        payload["type"] = self.item_type
        return payload


class CraftNarrative(BaseModel):
    name: str
    desc: str
