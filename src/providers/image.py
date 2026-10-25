"""Image-generation providers.

A narrow seam between the agent's ``generate_cover_illustration`` tool
and an external image API. We intentionally keep the protocol small —
prompt in, PNG file on disk out — so swapping providers later (Stability,
Replicate, a local Stable Diffusion daemon) is a one-file change.

The contract every provider implements:

- ``generate(prompt, output_path, size, quality) -> Path`` — write a PNG
  at ``output_path`` and return the same path. Raises
  ``ImageGenerationError`` for anything the caller shouldn't retry
  with the same inputs (auth failure, rate limit, policy rejection,
  missing SDK). Network blips should also surface as
  ``ImageGenerationError`` — the tool layer reports a clean message
  to the user rather than guessing at retry semantics.

Concrete providers: ``OpenAIImageProvider`` (``gpt-image-2``) and
``GoogleImageProvider`` (Gemini "Nano Banana"). Each also carries a
``LABEL`` and an approximate per-quality ``COST_USD`` table that the
cost-confirm prompt shows. SDK imports are lazy so a user on an
Ollama-only path doesn't need them installed.
"""

from __future__ import annotations

import base64
import io
import math
import os
from contextlib import suppress
from pathlib import Path
from typing import Protocol, runtime_checkable

from PIL import Image


class ImageGenerationError(Exception):
    """Raised when an ``ImageProvider.generate`` call can't produce a
    PNG the caller can use — auth failure, API error, empty response,
    missing SDK. The tool layer forwards the message to the user."""


@runtime_checkable
class ImageProvider(Protocol):
    LABEL: str
    COST_USD: dict[str, float]

    def generate(
        self,
        prompt: str,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
    ) -> Path: ...


# A high-quality portrait can take minutes, so the tight REPL-picker
# ping timeout won't do, but the SDK default (~600 s) would hang on a
# network drop. 300 s is the compromise; the agent's live progress
# indicator shows the user it is still working meanwhile.
_GENERATION_TIMEOUT_SECONDS = 300.0


class OpenAIImageProvider:
    """OpenAI ``gpt-image-2`` adapter.

    Uses the same API key the LLM provider uses — we assume the user
    has one OpenAI credential, not two. Responses come back as
    base64-encoded PNG; we decode and write them atomically (tmp file
    + ``os.replace``) so a Ctrl-C or disk-full mid-write can't leave a
    truncated file the renderer would later choke on.
    """

    # gpt-image-1 was retired: the API now answers "model does not exist".
    MODEL = "gpt-image-2"
    LABEL = "OpenAI gpt-image-2"
    # APPROXIMATE portrait 1024x1536 USD, checked 2026-10. Advisory only.
    COST_USD = {"low": 0.01, "medium": 0.05, "high": 0.17}

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def generate(
        self,
        prompt: str,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
    ) -> Path:
        try:
            from openai import (  # type: ignore[import-not-found]
                APIConnectionError,
                APIError,
                APITimeoutError,
                AuthenticationError,
                OpenAI,
                PermissionDeniedError,
            )
        except ImportError as e:
            raise ImageGenerationError(
                "The 'openai' SDK is missing from this install. Try: "
                "pip install --force-reinstall littlepress-ai"
            ) from e

        client = OpenAI(api_key=self._api_key, timeout=_GENERATION_TIMEOUT_SECONDS)

        try:
            response = client.images.generate(
                model=self.MODEL,
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )
        except (AuthenticationError, PermissionDeniedError) as e:
            raise ImageGenerationError(f"OpenAI rejected the request: {e}") from e
        except (APIConnectionError, APITimeoutError) as e:
            # Keep this branch *above* APIError — both inherit from it
            # in the SDK, and we want the network-specific message to
            # win so the user retries connectivity, not the prompt.
            raise ImageGenerationError(
                f"OpenAI image API could not be reached (network / timeout): {e}"
            ) from e
        except APIError as e:
            raise ImageGenerationError(f"OpenAI image generation failed: {e}") from e

        b64 = response.data[0].b64_json if response.data else None
        if not b64:
            raise ImageGenerationError(
                "OpenAI returned an empty image — often means the prompt "
                "hit a policy filter. Rephrase and try again."
            )

        try:
            png_bytes = base64.b64decode(b64, validate=True)
        except ValueError as e:
            # ``binascii.Error`` is a subclass of ``ValueError`` since
            # 3.2, so catching the parent covers both — Sonar S5713.
            raise ImageGenerationError(
                f"OpenAI returned malformed base64 image data: {e}"
            ) from e

        _atomic_write_bytes(output_path, png_bytes)
        return output_path


class GoogleImageProvider:
    """Gemini image-generation ("Nano Banana") adapter.

    Uses the same key as the Gemini chat provider. Gemini picks its own
    pixel size, so ``size`` only sets the aspect ratio and ``quality``
    picks the resolution tier. The reply may be JPEG; it is re-encoded
    to PNG so callers get the same file type from every provider.
    """

    MODEL = "gemini-nano-banana-2.1"
    LABEL = "Google Gemini (Nano Banana 2.1)"
    _IMAGE_SIZE = {"low": "1K", "medium": "2K", "high": "4K"}
    # APPROXIMATE per-image USD by tier, checked 2026-10. Advisory only.
    COST_USD = {"low": 0.04, "medium": 0.05, "high": 0.12}

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def generate(
        self,
        prompt: str,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
    ) -> Path:
        try:
            from google import genai  # type: ignore[import-not-found]
            from google.genai import types as gtypes  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImageGenerationError(
                "The 'google-genai' SDK is missing from this install. Try: "
                "pip install --force-reinstall littlepress-ai"
            ) from e

        width, height = (int(n) for n in size.split("x"))
        divisor = math.gcd(width, height)
        try:
            client = genai.Client(
                api_key=self._api_key,
                http_options=gtypes.HttpOptions(
                    timeout=int(_GENERATION_TIMEOUT_SECONDS * 1000)
                ),
            )
            response = client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=gtypes.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=gtypes.ImageConfig(
                        aspect_ratio=f"{width // divisor}:{height // divisor}",
                        image_size=self._IMAGE_SIZE.get(quality, "2K"),
                    ),
                ),
            )
        except Exception as e:  # noqa: BLE001 — SDK error classes vary by version
            raise ImageGenerationError(f"Gemini image generation failed: {e}") from e

        data = _first_inline_image(response)
        if data is None:
            raise ImageGenerationError(
                "Gemini returned no image — often means the prompt hit a "
                "safety filter. Rephrase and try again."
            )
        try:
            with Image.open(io.BytesIO(data)) as img:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
        except OSError as e:
            raise ImageGenerationError(
                f"Gemini returned unreadable image data: {e}"
            ) from e
        _atomic_write_bytes(output_path, buf.getvalue())
        return output_path


def _first_inline_image(response) -> bytes | None:
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            blob = getattr(part, "inline_data", None)
            if blob is not None and getattr(blob, "data", None):
                return blob.data
    return None


def _atomic_write_bytes(output_path: Path, data: bytes) -> None:
    """Write ``data`` to ``output_path`` atomically.

    Matches the pattern used by ``src/memory.py``: write to a sibling
    ``.tmp`` file, then ``os.replace`` to the final name so readers
    never see partial content. On failure (disk full, OS error), the
    ``.tmp`` file is cleaned up and the exception re-raised as
    ``ImageGenerationError`` so every generate() failure mode exits
    through one door.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        tmp_path.write_bytes(data)
        os.replace(tmp_path, output_path)
    except OSError as e:
        # Best-effort cleanup; a leftover .tmp is harmless but ugly.
        with suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise ImageGenerationError(
            f"Could not write image to {output_path}: {e}"
        ) from e
