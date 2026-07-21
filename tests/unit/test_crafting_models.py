from llm_rpg_server.crafting import CraftPropertyProposal


def test_property_proposal_accepts_openai_compatible_properties_envelope():
    proposal = CraftPropertyProposal.model_validate({
        "properties": {
            "can_be_ingredient": True,
            "tradable": False,
            "tags": ["refined", "shelter", "wooden", "animalhide"],
            "properties": {},
        },
    })

    assert proposal.can_be_ingredient is True
    assert proposal.tradable is False
    assert proposal.tags == ["refined", "shelter", "wooden", "animalhide"]
    assert proposal.properties == {}


def test_property_proposal_leaves_regular_properties_unchanged():
    proposal = CraftPropertyProposal.model_validate({
        "can_be_ingredient": False,
        "tags": ["healing"],
        "properties": {"val": 42, "effect_type": "heal_hp"},
    })

    assert proposal.can_be_ingredient is False
    assert proposal.tags == ["healing"]
    assert proposal.properties == {"val": 42, "effect_type": "heal_hp"}
