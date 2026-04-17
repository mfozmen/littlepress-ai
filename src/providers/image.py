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

The only concrete provider today is ``OpenAIImageProvider`` (model
``gpt-image-1``). The SDK import is lazy so a user on an Ollama-only
path doesn't need it installed.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Protocol, runtime_checkable


class ImageGenerationError(Exception):
    """Raised when an ``ImageProvider.generate`` call can't produce a
    PNG the caller can use — auth failure, API error, empty response,
    missing SDK. The tool layer forwards the message to the user."""


@runtime_checkable
class ImageProvider(Protocol):
    def generate(
        self,
        prompt: str,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
    ) -> Path: ...


# ``gpt-image-1`` at ``quality="high"`` legitimately takes 30-90 s, so
# we can't use the tight REPL-picker ping timeout. But the SDK default
# (~600 s) would hang the REPL forever on a network drop. 120 s is the
# compromise: long enough for a slow high-quality render, short enough
# that a dead connection reaches the user while they're still at the
# prompt.
_GENERATION_TIMEOUT_SECONDS = 120.0


class OpenAIImageProvider:
    """OpenAI ``gpt-image-1`` adapter.

    Uses the same API key the LLM provider uses — we assume the user
    has one OpenAI credential, not two. Responses come back as
    base64-encoded PNG; we decode and write them atomically (tmp file
    + ``os.replace``) so a Ctrl-C or disk-full mid-write can't leave a
    truncated file the renderer would later choke on.
    """

    MODEL = "gpt-image-1"

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


def _atomic_write_bytes(output_path: Path, data: bytes) -> None:
    """Write ``data`` to ``output_path`` atomically.

    Matches the pattern used by ``src/memory.py`` and
    ``src/draft.py::atomic_copy``: write to a sibling ``.tmp`` file,
    then ``os.replace`` to the final name so readers never see partial
    content. On failure (disk full, OS error), the ``.tmp`` file is
    cleaned up and the exception re-raised as ``ImageGenerationError``
    so every generate() failure mode exits through one door.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        tmp_path.write_bytes(data)
        os.replace(tmp_path, output_path)
    except OSError as e:
        # Best-effort cleanup; a leftover .tmp is harmless but ugly.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ImageGenerationError(
            f"Could not write image to {output_path}: {e}"
        ) from e
