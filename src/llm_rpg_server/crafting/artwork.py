from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlparse

import httpx

from llm_rpg_server.shared.config import ContentProvider, Settings

from .models import CraftArtwork, CraftConcept

LOGGER = logging.getLogger(__name__)


class CraftImageError(RuntimeError):
    pass


class ObjectStorageGateway(Protocol):
    def download(self, key: str) -> tuple[bytes, str]: ...

    def upload(self, key: str, data: bytes, content_type: str) -> None: ...


class HttpOssGateway:
    """Small adapter for the OSS upload/download endpoints used by the image service."""

    def __init__(self, settings: Settings):
        self.base_url = settings.craft_oss_base_url.rstrip("/")
        self.bucket = settings.craft_oss_bucket

    def download(self, key: str) -> tuple[bytes, str]:
        response = httpx.get(self._file_url(key), timeout=30.0)
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "application/octet-stream")

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        if not self.bucket:
            raise CraftImageError("CRAFT_OSS_BUCKET must be configured for crafted images")
        response = httpx.post(
            f"{self.base_url}/upload",
            data={"bucket_name": self.bucket, "key": key, "content_type": content_type},
            files={"file": (Path(key).name or "crafted-image", data, content_type)},
            timeout=60.0,
        )
        response.raise_for_status()

    def _file_url(self, key: str) -> str:
        if not self.bucket:
            raise CraftImageError("CRAFT_OSS_BUCKET must be configured for crafted images")
        return f"{self.base_url}/file/{quote(self.bucket, safe='')}/{quote(key, safe='/')}"


class CraftImageSourceResolver:
    """Turns catalog image references into stable raster OSS object keys."""

    def __init__(self, storage: ObjectStorageGateway, assets_root: Path):
        self.storage = storage
        self.assets_root = assets_root.resolve()

    def resolve(self, definition: dict[str, Any]) -> str:
        image_key = definition.get("image_key")
        if isinstance(image_key, str) and image_key.strip():
            return image_key.strip()
        data, content_type = self._load_source(definition)
        if content_type in {"image/svg+xml", "image/svg"}:
            data, content_type = self._rasterize_svg(data), "image/png"
        extension = mimetypes.guess_extension(content_type.split(";", 1)[0]) or ".png"
        digest = hashlib.sha256(data).hexdigest()
        key = f"crafting/sources/{digest}{extension}"
        self.storage.upload(key, data, content_type)
        return key

    def _load_source(self, definition: dict[str, Any]) -> tuple[bytes, str]:
        image_url = str(definition.get("image_url") or "").strip()
        if image_url.startswith("/assets/"):
            return self._load_local_asset(image_url, str(definition.get("name") or "Crafting ingredient"))
        if image_url.startswith(("https://", "http://")):
            response = httpx.get(image_url, timeout=30.0)
            response.raise_for_status()
            return response.content, response.headers.get("content-type", "application/octet-stream")
        return self._placeholder(str(definition.get("name") or "Crafting ingredient"))

    def _load_local_asset(self, image_url: str, fallback_name: str) -> tuple[bytes, str]:
        asset_path = (self.assets_root / unquote(urlparse(image_url).path).lstrip("/")).resolve()
        if self.assets_root not in asset_path.parents:
            raise CraftImageError(f"Crafting image asset is unavailable: {image_url}")
        if not asset_path.is_file():
            LOGGER.warning(
                "Crafting image asset %s was not found under %s; using a generated placeholder",
                image_url,
                self.assets_root,
            )
            return self._placeholder(fallback_name)
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        return asset_path.read_bytes(), content_type

    @staticmethod
    def _rasterize_svg(data: bytes) -> bytes:
        try:
            import cairosvg
        except ImportError as exc:  # pragma: no cover - dependency is enforced in pyproject
            raise CraftImageError("cairosvg is required to use SVG crafting source assets") from exc
        return cairosvg.svg2png(bytestring=data, output_width=768, output_height=768)

    @staticmethod
    def _placeholder(name: str) -> tuple[bytes, str]:
        try:
            from PIL import Image, ImageDraw
        except ImportError as exc:  # pragma: no cover - dependency is enforced in pyproject
            raise CraftImageError("Pillow is required to create crafting placeholder images") from exc
        digest = hashlib.sha256(name.encode("utf-8")).digest()
        image = Image.new("RGB", (768, 768), (digest[0], digest[1], digest[2]))
        draw = ImageDraw.Draw(image)
        draw.ellipse((144, 144, 624, 624), fill=(255 - digest[0], 255 - digest[1], 255 - digest[2]))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), "image/png"


class CraftArtworkGenerator(Protocol):
    def generate(
            self,
            first: dict[str, Any],
            second: dict[str, Any],
            concept: CraftConcept,
    ) -> CraftArtwork: ...


class OssCraftArtworkGenerator:
    """Image-service adapter with a deterministic diagonal-composite fallback."""

    def __init__(
            self,
            content: ContentProvider,
            settings: Settings,
            storage: ObjectStorageGateway | None = None,
            sources: CraftImageSourceResolver | None = None,
    ):
        self.content = content
        self.settings = settings
        self.storage = storage or HttpOssGateway(settings)
        self.sources = sources or CraftImageSourceResolver(self.storage, settings.craft_web_assets_root)

    def generate(self, first: dict[str, Any], second: dict[str, Any], concept: CraftConcept) -> CraftArtwork:
        first_key = self.sources.resolve(first)
        second_key = self.sources.resolve(second)
        try:
            image_key = self._generate_with_service(first_key, second_key, concept)
            image_url = self.storage._file_url(image_key)
            return CraftArtwork(image_url=image_url, image_key=image_key, status="generated")
        except Exception:
            image_key, image_url = self._compose_fallback(first_key, second_key)
            return CraftArtwork(image_url=image_url, image_key=image_key, status="fallback")

    def _generate_with_service(self, first_key: str, second_key: str, concept: CraftConcept) -> str:
        if not self.settings.craft_image_service_url:
            raise CraftImageError("CRAFT_IMAGE_SERVICE_URL must be configured for AI crafting images")
        service_url = self.settings.craft_image_service_url.rstrip("/")
        definition = self.content.prompt("crafting_image")
        prompt = "\n\n".join((
            definition.system,
            definition.user.format(name=concept.name, description=concept.desc, category=concept.category),
        ))
        response = httpx.post(
            f"{service_url}/api/v1/task/edit",
            json={
                "bucket_name": self.settings.craft_oss_bucket,
                "file_keys": [first_key, second_key],
                "prompt": prompt,
                "guidance_scale": self.settings.craft_image_guidance_scale,
                "steps": self.settings.craft_image_steps,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        task_id = str(response.json()["task_id"])
        deadline = time.monotonic() + self.settings.craft_image_timeout_seconds
        while time.monotonic() < deadline:
            status_response = httpx.get(f"{service_url}/api/v1/task/status/{task_id}", timeout=30.0)
            status_response.raise_for_status()
            status = status_response.json()
            if status.get("status") == "completed":
                return self._key_from_completed_task(status)
            if status.get("status") == "failed":
                raise CraftImageError(str(status.get("error_message") or "Craft image task failed"))
            time.sleep(self.settings.craft_image_poll_interval_seconds)
        raise CraftImageError("Craft image task timed out")

    def _key_from_completed_task(self, status: dict[str, Any]) -> str:
        direct_key = status.get("result_key")
        if isinstance(direct_key, str) and direct_key:
            return direct_key
        result_url = status.get("result_url")
        if not isinstance(result_url, str) or not result_url:
            raise CraftImageError("Completed craft image task did not return a result URL")
        path = unquote(urlparse(result_url).path).lstrip("/")
        prefix = f"file/{self.settings.craft_oss_bucket}/"
        if path.startswith(prefix):
            return path[len(prefix):]
        raise CraftImageError("Craft image result URL cannot be converted to an OSS key")

    def _compose_fallback(self, first_key: str, second_key: str) -> (str, str):
        try:
            from PIL import Image, ImageDraw, ImageOps
        except ImportError as exc:  # pragma: no cover - dependency is enforced in pyproject
            raise CraftImageError("Pillow is required to compose fallback crafting images") from exc
        first_bytes, _ = self.storage.download(first_key)
        second_bytes, _ = self.storage.download(second_key)
        with Image.open(io.BytesIO(first_bytes)) as first_source, Image.open(io.BytesIO(second_bytes)) as second_source:
            size = (768, 768)
            first_image = ImageOps.fit(first_source.convert("RGBA"), size, method=Image.Resampling.LANCZOS)
            second_image = ImageOps.fit(second_source.convert("RGBA"), size, method=Image.Resampling.LANCZOS)
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).polygon([(0, 0), (size[0], 0), (0, size[1])], fill=255)
        composite = first_image.copy()
        composite.paste(second_image, (0, 0), mask)
        buffer = io.BytesIO()
        composite.save(buffer, format="PNG")
        key = f"outputs/crafting/fallback/{uuid.uuid4().hex}.png"
        url = self.storage._file_url(key)
        self.storage.upload(key, buffer.getvalue(), "image/png")
        return key, url
