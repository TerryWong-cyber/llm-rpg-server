from llm_rpg_server.bootstrap import build_container


def test_application_container_builds():
    container = build_container()

    assert container.combat.engine.app is not None
