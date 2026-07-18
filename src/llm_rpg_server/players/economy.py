from __future__ import annotations

from typing import Protocol

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.shared.config import ContentProvider

from .models import PlayerProfile
from .repository import PlayerRepository


class TradeAccessPolicy(Protocol):
    def require_trade_access(self, player_id: str) -> None: ...


class EconomyService:
    def __init__(
        self,
        players: PlayerRepository,
        catalog: Catalog,
        content: ContentProvider,
        access_policy: TradeAccessPolicy | None = None,
    ):
        self.players = players
        self.catalog = catalog
        self.content = content
        self.access_policy = access_policy

    def set_access_policy(self, access_policy: TradeAccessPolicy) -> None:
        self.access_policy = access_policy

    def buy(self, player_id: str, item_type: str, item_id: str) -> PlayerProfile:
        if self.access_policy:
            self.access_policy.require_trade_access(player_id)
        definition = self.catalog.item_definition(item_type, item_id)
        if item_type not in {"weapon", "armor", "item"} or definition is None:
            raise ValueError(self.content.text("errors.inventory.invalid_item"))
        if not definition.get("tradable", True):
            raise ValueError(self.content.text("errors.inventory.not_tradable"))
        with self.players.transaction(player_id) as profile:
            inventory = profile.inventory
            if item_type == "weapon" and item_id in inventory.weapons:
                raise ValueError(self.content.text("errors.shop.owned"))
            if item_type == "armor" and item_id in inventory.armors:
                raise ValueError(self.content.text("errors.shop.owned"))
            price = int(definition["value"])
            if profile.gold < price:
                raise ValueError(self.content.text("errors.shop.gold"))
            profile.gold -= price
            if item_type == "weapon":
                inventory.weapons.append(item_id)
            elif item_type == "armor":
                inventory.armors.append(item_id)
            else:
                inventory.items[item_id] = inventory.items.get(item_id, 0) + 1
        return self.players.get(player_id)

    def sell(self, player_id: str, item_type: str, item_id: str) -> PlayerProfile:
        if self.access_policy:
            self.access_policy.require_trade_access(player_id)
        definition = self.catalog.item_definition(item_type, item_id)
        if definition is None:
            raise ValueError(self.content.text("errors.inventory.invalid_item"))
        if not definition.get("tradable", True):
            raise ValueError(self.content.text("errors.inventory.not_tradable"))
        with self.players.transaction(player_id) as profile:
            inventory = profile.inventory
            if item_type == "weapon":
                if item_id not in inventory.weapons:
                    raise ValueError(self.content.text("errors.inventory.not_owned"))
                if profile.equipped_weapon_id == item_id:
                    raise ValueError(self.content.text("errors.inventory.equipped"))
                if len(inventory.weapons) <= 1:
                    raise ValueError(self.content.text("errors.inventory.keep_weapon"))
                inventory.weapons.remove(item_id)
            elif item_type == "armor":
                if item_id not in inventory.armors:
                    raise ValueError(self.content.text("errors.inventory.not_owned"))
                if profile.equipped_armor_id == item_id:
                    raise ValueError(self.content.text("errors.inventory.equipped"))
                if item_id == "0":
                    raise ValueError(self.content.text("errors.inventory.starter_armor"))
                if len(inventory.armors) <= 1:
                    raise ValueError(self.content.text("errors.inventory.keep_armor"))
                inventory.armors.remove(item_id)
            elif item_type == "item":
                if inventory.items.get(item_id, 0) <= 0:
                    raise ValueError(self.content.text("errors.inventory.not_owned"))
                inventory.items[item_id] -= 1
            elif item_type == "material":
                if inventory.materials.get(item_id, 0) <= 0:
                    raise ValueError(self.content.text("errors.inventory.not_owned"))
                inventory.materials[item_id] -= 1
                if inventory.materials[item_id] == 0:
                    del inventory.materials[item_id]
            else:
                raise ValueError(self.content.text("errors.inventory.invalid_type"))
            profile.gold += int(definition["value"]) // 2
        return self.players.get(player_id)
