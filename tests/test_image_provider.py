"""Unit tests for src/providers/image.py.

The image provider is the thin seam between the agent's
``generate_cover_illustration`` tool and the outside world (an image
API). Tests monkeypatch the ``openai`` SDK surface so the suite never
hits the network — the contract we care about is "our caller gets a
PNG file on disk at the path they asked for, or a typed error."
"""

from __future__ import annotations

import base64
import sys
import types
from pathlib import Path

import pytest

from src.providers.image import (
    ImageGenerationError,
    ImageProvider,
    OpenAIImageProvider,
)

# A 1-byte stand-in for real PNG bytes. The provider treats the
# response body as opaque — it base64-decodes and writes the result
# verbatim — so the actual content doesn't matter for these tests.
_FAKE_PNG_BYTES = b"\x89PNG-stub"


def _install_fake_openai(monkeypatch, *, generate, client_init=None):
    """Install a fake ``openai`` module whose ``OpenAI(...).images.generate``
    calls the ``generate`` callable with the kwargs the provider passed.

    ``generate`` returns whatever object should come back from the SDK
    (the provider reads ``response.data[0].b64_json``). ``client_init``
    is an optional callback that receives the kwargs the provider
    passed to ``openai.OpenAI(...)`` — used by timeout tests."""
    fake = types.SimpleNamespace()

    class _Images:
        def generate(self, **kwargs):
            return generate(**kwargs)

    class _Client:
        def __init__(self, **kwargs):
            if client_init is not None:
                client_init(**kwargs)
            self.images = _Images()

    fake.OpenAI = _Client
    fake.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake.PermissionDeniedError = type(
        "PermissionDeniedError", (fake.AuthenticationError,), {}
    )
    fake.APIError = type("APIError", (Exception,), {})
    fake.APIConnectionError = type("APIConnectionError", (fake.APIError,), {})
    fake.APITimeoutError = type("APITimeoutError", (fake.APIConnectionError,), {})
    monkeypatch.setitem(sys.modules, "openai", fake)
    return fake


def _b64_response(png_bytes: bytes):
    """Build a stand-in for the SDK's ``ImagesResponse`` — the provider
    only reads ``.data[0].b64_json``, so a SimpleNamespace is enough."""
    entry = types.SimpleNamespace(b64_json=base64.b64encode(png_bytes).decode())
    return types.SimpleNamespace(data=[entry])


def test_openai_image_provider_implements_image_provider_protocol():
    """runtime_checkable protocol — ``isinstance`` should pass without
    instantiation side effects (no network call, no key needed)."""
    provider = OpenAIImageProvider(api_key="sk-test")
    assert isinstance(provider, ImageProvider)


def test_openai_image_provider_writes_png_bytes_to_output_path(
    tmp_path, monkeypatch
):
    """Happy path: the b64-decoded response body lands at ``output_path``
    verbatim, and the path is returned so the caller can chain."""
    _install_fake_openai(
        monkeypatch,
        generate=lambda **_: _b64_response(_FAKE_PNG_BYTES),
    )

    provider = OpenAIImageProvider(api_key="sk-test")
    out = tmp_path / "cover.png"
    result = provider.generate(
        prompt="a watercolour dinosaur chick",
        output_path=out,
    )

    assert result == out
    assert out.read_bytes() == _FAKE_PNG_BYTES


def test_openai_image_provider_forwards_prompt_size_quality(
    tmp_path, monkeypatch
):
    """The caller's prompt / size / quality must reach the SDK
    verbatim — no silent substitution or defaulting above the
    provider's documented defaults."""
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _b64_response(_FAKE_PNG_BYTES)

    _install_fake_openai(monkeypatch, generate=_capture)

    provider = OpenAIImageProvider(api_key="sk-test")
    provider.generate(
        prompt="a brave owl at dusk",
        output_path=tmp_path / "o.png",
        size="1024x1536",
        quality="high",
    )

    assert captured["prompt"] == "a brave owl at dusk"
    assert captured["size"] == "1024x1536"
    assert captured["quality"] == "high"
    assert captured["model"] == "gpt-image-1"


def test_openai_image_provider_creates_parent_dir_for_output(
    tmp_path, monkeypatch
):
    """.book-gen/images/ won't exist on first generate — provider must
    create the directory rather than raising FileNotFoundError.
    Mirrors memory/atomic_copy which also mkdir(parents=True)."""
    _install_fake_openai(
        monkeypatch,
        generate=lambda **_: _b64_response(_FAKE_PNG_BYTES),
    )

    provider = OpenAIImageProvider(api_key="sk-test")
    nested = tmp_path / "sub" / "deeper" / "cover.png"
    provider.generate(prompt="x", output_path=nested)

    assert nested.read_bytes() == _FAKE_PNG_BYTES


def test_openai_image_provider_wraps_auth_error_as_generation_error(
    tmp_path, monkeypatch
):
    """``AuthenticationError`` from the SDK means the key is dead —
    surface a typed ``ImageGenerationError`` so the tool can report a
    clean message instead of leaking SDK internals."""
    fake = _install_fake_openai(monkeypatch, generate=lambda **_: None)

    def _boom(**_):
        raise fake.AuthenticationError("invalid api key")

    fake.OpenAI = lambda api_key, **_: types.SimpleNamespace(
        images=types.SimpleNamespace(generate=_boom)
    )

    provider = OpenAIImageProvider(api_key="sk-bad")
    with pytest.raises(ImageGenerationError) as exc:
        provider.generate(prompt="x", output_path=tmp_path / "o.png")
    assert "invalid api key" in str(exc.value).lower()


def test_openai_image_provider_wraps_api_error_as_generation_error(
    tmp_path, monkeypatch
):
    """Rate limits / 5xx / billing errors all surface as the same
    typed wrapper — the tool layer doesn't need to distinguish."""
    fake = _install_fake_openai(monkeypatch, generate=lambda **_: None)

    def _boom(**_):
        raise fake.APIError("rate limit")

    fake.OpenAI = lambda api_key, **_: types.SimpleNamespace(
        images=types.SimpleNamespace(generate=_boom)
    )

    provider = OpenAIImageProvider(api_key="sk-test")
    with pytest.raises(ImageGenerationError) as exc:
        provider.generate(prompt="x", output_path=tmp_path / "o.png")
    assert "rate limit" in str(exc.value).lower()


def test_openai_image_provider_empty_response_raises_generation_error(
    tmp_path, monkeypatch
):
    """Some SDK versions return an entry with ``b64_json=None`` when
    the policy filter rejects a prompt (no error, but no image either).
    Treat that as a generation failure rather than writing an empty
    file the renderer will later choke on."""
    empty = types.SimpleNamespace(
        data=[types.SimpleNamespace(b64_json=None)]
    )
    _install_fake_openai(monkeypatch, generate=lambda **_: empty)

    provider = OpenAIImageProvider(api_key="sk-test")
    with pytest.raises(ImageGenerationError):
        provider.generate(prompt="x", output_path=tmp_path / "o.png")


def test_openai_image_provider_missing_sdk_raises_generation_error(
    tmp_path, monkeypatch
):
    """The openai SDK is a default dep, but users installing in minimal
    environments could still trip this. Surface a clear reinstall hint
    rather than a raw ImportError deep in the stack."""
    monkeypatch.setitem(sys.modules, "openai", None)

    provider = OpenAIImageProvider(api_key="sk-test")
    with pytest.raises(ImageGenerationError) as exc:
        provider.generate(prompt="x", output_path=tmp_path / "o.png")
    assert "openai" in str(exc.value).lower()


def test_openai_image_provider_passes_timeout_to_client(tmp_path, monkeypatch):
    """Image generation at quality=high legitimately takes 30–90 s, but
    the SDK default (~600 s) would hang the REPL forever on a network
    drop. The provider must pin an explicit timeout at client
    construction so a hung call surfaces as a typed error in bounded
    time, matching the pattern used by every LLM provider in
    src/providers/llm.py."""
    captured: dict = {}

    def _capture_init(**kwargs):
        captured.update(kwargs)

    _install_fake_openai(
        monkeypatch,
        generate=lambda **_: _b64_response(_FAKE_PNG_BYTES),
        client_init=_capture_init,
    )

    provider = OpenAIImageProvider(api_key="sk-test")
    provider.generate(prompt="x", output_path=tmp_path / "o.png")

    assert "timeout" in captured
    assert isinstance(captured["timeout"], (int, float))
    # Some room to breathe for high-quality renders, but not the SDK
    # default (~600 s) which would hang the REPL on a network drop.
    assert 30 <= captured["timeout"] <= 300


def test_openai_image_provider_maps_connection_error_to_generation_error(
    tmp_path, monkeypatch
):
    """A connection / timeout error is distinct from an API policy
    rejection — the module docstring promises network blips surface
    cleanly. Wrap them specifically so the message tells the user the
    API couldn't be reached, not that the prompt was rejected."""
    fake = _install_fake_openai(monkeypatch, generate=lambda **_: None)

    def _boom(**_):
        raise fake.APIConnectionError("network unreachable")

    fake.OpenAI = lambda **_: types.SimpleNamespace(
        images=types.SimpleNamespace(generate=_boom)
    )

    provider = OpenAIImageProvider(api_key="sk-test")
    with pytest.raises(ImageGenerationError) as exc:
        provider.generate(prompt="x", output_path=tmp_path / "o.png")
    # Message differentiates connectivity from policy rejection so the
    # user retries network, not the prompt.
    assert (
        "connection" in str(exc.value).lower()
        or "network" in str(exc.value).lower()
        or "reach" in str(exc.value).lower()
    )


def test_openai_image_provider_wraps_malformed_base64(tmp_path, monkeypatch):
    """If the SDK ever hands back a ``b64_json`` that isn't valid
    base64, ``b64decode`` raises ``binascii.Error`` — an untyped
    exception that would escape to the agent loop. Wrap it as
    ``ImageGenerationError`` so every failure mode exits through one
    door."""
    malformed = types.SimpleNamespace(
        data=[types.SimpleNamespace(b64_json="not-valid-base64!!!???")]
    )
    _install_fake_openai(monkeypatch, generate=lambda **_: malformed)

    provider = OpenAIImageProvider(api_key="sk-test")
    with pytest.raises(ImageGenerationError):
        provider.generate(prompt="x", output_path=tmp_path / "o.png")


def test_openai_image_provider_write_is_atomic(tmp_path, monkeypatch):
    """The docstring promises atomic writes. If the byte stream can't
    make it to the final path — Ctrl-C, disk full, OS error mid-write —
    the caller must see either a finished file or no file at all.
    Never a truncated PNG the renderer would later choke on."""
    _install_fake_openai(
        monkeypatch,
        generate=lambda **_: _b64_response(_FAKE_PNG_BYTES),
    )

    final_path = tmp_path / "cover.png"

    # Make the final ``os.replace`` call fail, simulating disk full at
    # the commit step. The .tmp sibling may or may not exist briefly,
    # but the final path must not contain a half-written file.
    import os as _os

    real_replace = _os.replace

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("src.providers.image.os.replace", _boom)

    provider = OpenAIImageProvider(api_key="sk-test")
    with pytest.raises(ImageGenerationError):
        provider.generate(prompt="x", output_path=final_path)

    assert not final_path.exists(), (
        "atomic write contract: the final path must never contain "
        "partial bytes when the commit step fails."
    )
    # Restore in case further tests need it (monkeypatch unwinds, but
    # keeping the binding available clarifies intent).
    _ = real_replace
