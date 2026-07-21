from pathlib import Path

import pytest

from llm_rpg_server.crafting.artwork import CraftImageError, CraftImageSourceResolver


class RecordingStorage:
    def __init__(self):
        self.uploads: list[tuple[str, bytes, str]] = []

    def download(self, key: str) -> tuple[bytes, str]:
        raise AssertionError("download is not expected while resolving a source")

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        self.uploads.append((key, data, content_type))


def test_missing_local_asset_uses_placeholder_instead_of_failing_craft(tmp_path: Path):
    storage = RecordingStorage()
    resolver = CraftImageSourceResolver(storage, tmp_path)

    key = resolver.resolve({
        "name": "Coal",
        "image_url": "/assets/vendor/game-icons/delapouite/coal-pile.svg",
    })

    assert key.startswith("crafting/sources/")
    assert key.endswith(".png")
    assert len(storage.uploads) == 1
    uploaded_key, uploaded_data, content_type = storage.uploads[0]
    assert uploaded_key == key
    assert uploaded_data.startswith(b"\x89PNG\r\n\x1a\n")
    assert content_type == "image/png"


def test_local_asset_path_cannot_escape_configured_root(tmp_path: Path):
    resolver = CraftImageSourceResolver(RecordingStorage(), tmp_path)

    with pytest.raises(CraftImageError, match="unavailable"):
        resolver.resolve({
            "name": "Unsafe",
            "image_url": "/assets/../../outside.png",
        })
