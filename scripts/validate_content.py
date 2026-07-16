from llm_rpg_server.bootstrap import build_container, validate_references


def main() -> None:
    container = build_container()
    validate_references(container)


if __name__ == "__main__":
    main()

