from __future__ import annotations

import uuid

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.shared.config import ContentProvider

from .models import Inventory, PlayerProfile
from .repository import PlayerRepository


class PlayerService:
    def __init__(self, repository: PlayerRepository, catalog: Catalog, content: ContentProvider):
        self.repository = repository
        self.catalog = catalog
        self.content = content

    def create(self, name: str, race_id: str) -> PlayerProfile:
        if race_id not in self.catalog.races:
            raise ValueError(self.content.text("errors.character.invalid"))
        defaults = self.content.document("catalog/default_player.json")
        profile = self._new_profile(f"char_{uuid.uuid4().hex[:8]}", name, race_id, defaults)
        return self.repository.create(profile)

    def ensure(self, player_id: str, name: str | None = None, race_id: str | None = None) -> PlayerProfile:
        if self.repository.exists(player_id):
            return self.repository.get(player_id)
        defaults = self.content.document("catalog/default_player.json")
        return self.repository.create(self._new_profile(
            player_id,
            name or defaults["fallback_name"],
            race_id or defaults["race_id"],
            defaults,
        ))

    def set_equipment(
        self,
        player_id: str,
        item_type: str,
        item_id: str | None,
    ) -> PlayerProfile:
        if item_type not in {"weapon", "armor"}:
            raise ValueError(self.content.text("errors.inventory.invalid_type"))
        with self.repository.transaction(player_id) as profile:
            collection = profile.inventory.weapons if item_type == "weapon" else profile.inventory.armors
            catalog = self.catalog.weapons if item_type == "weapon" else self.catalog.armors
            if item_id is not None and (item_id not in collection or item_id not in catalog):
                raise ValueError(self.content.text("errors.inventory.not_owned"))
            if item_type == "weapon":
                profile.equipped_weapon_id = item_id
            else:
                profile.equipped_armor_id = item_id
                self.recalculate_resources(profile)
        return self.repository.get(player_id)

    def _new_profile(
        self,
        player_id: str,
        name: str,
        race_id: str,
        defaults: dict,
    ) -> PlayerProfile:
        race = self.catalog.races[race_id]
        inventory = Inventory.model_validate(defaults["inventory"])
        profile = PlayerProfile(
            player_id=player_id,
            name=name,
            character_id=str(defaults["character_id"]),
            race_id=race_id,
            experience_to_next=int(
                self.content.document("progression/rules.json")["level_curve"]["base_experience"]
            ),
            attributes=race["base_attributes"],
            gold=int(self.catalog.rules["initial_gold"]),
            inventory=inventory,
            psychological_traits=[
                *race.get("traits", []),
                *race.get("strengths", []),
                *race.get("weaknesses", []),
            ],
            equipped_weapon_id=inventory.weapons[0] if inventory.weapons else None,
            equipped_armor_id=inventory.armors[0] if inventory.armors else None,
        )
        self.recalculate_resources(profile, restore_gains=True)
        return profile

    def combat_character(self, profile: PlayerProfile) -> dict:
        race = self.catalog.races[profile.race_id]
        resources = self.content.document("progression/rules.json")["base_resources"]
        return {
            "id": f"player:{profile.player_id}",
            "name": f"{race['name']}旅人",
            "hp": int(resources["hp"]),
            "mp": int(resources["mp"]),
            "vit": profile.attributes.vitality,
            "str": profile.attributes.strength,
            "agi": profile.attributes.agility,
            "int": profile.attributes.wisdom,
            "wis": profile.attributes.wisdom,
            "luck": profile.attributes.luck,
            "traits": list(profile.psychological_traits),
            "desc": race["background"],
            "image_url": race.get("image_url"),
            "bonuses": dict(race.get("bonuses", {})),
            "conditional_modifiers": list(race.get("conditional_modifiers", [])),
            "passives": dict(race.get("passives", {})),
            "race_id": profile.race_id,
            "level": profile.level,
        }

    def recalculate_resources(
        self,
        profile: PlayerProfile,
        *,
        restore_gains: bool = False,
    ) -> None:
        character = self.combat_character(profile)
        rules = self.content.document("combat/rules.json")["attributes"]
        armor = self.catalog.armors.get(profile.equipped_armor_id or "", {})
        max_hp = max(1, int(
            character["hp"]
            + character["str"] * float(rules["hp_per_strength"])
            + character["vit"] * float(rules["hp_per_vitality"])
            + int(armor.get("hp_bonus", 0))
        ))
        max_mp = max(0, int(
            character["mp"] + character["wis"] * float(rules["mp_per_wisdom"])
        ))
        max_stamina = max(1, int(
            float(rules["base_stamina"])
            + character["str"] * float(rules["stamina_per_strength"])
            + character["agi"] * float(rules["stamina_per_agility"])
        ))
        hp_gain = max(0, max_hp - profile.max_hp)
        mp_gain = max(0, max_mp - profile.max_mp)
        stamina_gain = max(0, max_stamina - profile.max_stamina)
        profile.max_hp, profile.max_mp, profile.max_stamina = max_hp, max_mp, max_stamina
        if restore_gains:
            profile.current_hp = min(max_hp, profile.current_hp + hp_gain)
            profile.current_mp = min(max_mp, profile.current_mp + mp_gain)
            profile.stamina = min(max_stamina, profile.stamina + stamina_gain)
        else:
            profile.current_hp = min(max_hp, profile.current_hp)
            profile.current_mp = min(max_mp, profile.current_mp)
            profile.stamina = min(max_stamina, profile.stamina)
