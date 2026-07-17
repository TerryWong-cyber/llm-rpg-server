def test_content_documents_validate(content):
    content.validate()


def test_catalog_has_starter_armor(catalog):
    assert "0" in catalog.armors


def test_catalog_exposes_curated_game_icons(catalog):
    public = catalog.public_view()
    expected = {
        "characters": {
            "1": "delapouite/barbarian.svg",
            "2": "delapouite/pirate-captain.svg",
            "3": "delapouite/wizard-face.svg",
            "4": "delapouite/cleopatra.svg",
            "5": "delapouite/woman-elf-face.svg",
            "6": "delapouite/vampire-dracula.svg",
        },
        "weapons": {
            "1": "skoll/gladius.svg",
            "2": "lorc/crossed-swords.svg",
            "3": "lorc/crystal-wand.svg",
            "4": "delapouite/tribal-shield.svg",
            "5": "delapouite/bow-arrow.svg",
            "6": "lorc/wizard-staff.svg",
        },
        "armors": {
            "0": "lucasms/shirt.svg",
            "1": "lucasms/shirt.svg",
            "2": "lorc/plastron.svg",
            "3": "delapouite/cape.svg",
            "4": "lorc/robe.svg",
            "5": "delapouite/tribal-gear.svg",
            "6": "lorc/crystal-growth.svg",
        },
        "items": {
            "1": "lorc/potion-ball.svg",
            "2": "lorc/round-bottom-flask.svg",
            "3": "lorc/fire-bomb.svg",
            "4": "delapouite/bolt-bomb.svg",
            "5": "delapouite/olive.svg",
            "6": "lorc/round-bottom-flask.svg",
        },
        "resources": {
            "mat_1": "delapouite/log.svg",
            "mat_2": "lorc/rock.svg",
            "mat_3": "delapouite/mushrooms.svg",
            "mat_4": "darkzaitzev/fried-fish.svg",
            "mat_5": "lorc/crystal-bars.svg",
            "mat_6": "delapouite/olive.svg",
        },
    }
    prefix = "/assets/vendor/game-icons/"
    for collection, mappings in expected.items():
        for item_id, relative_path in mappings.items():
            assert public[collection][item_id]["image_url"] == prefix + relative_path
