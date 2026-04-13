"""Unit tests for src/providers/validator.py."""

import pytest

from src.providers import validator
from src.providers.llm import find


def test_none_provider_is_always_valid():
    # "none" = offline; there's nothing to validate.
    validator.validate_key(find("none"), "")


def test_ollama_has_nothing_to_validate():
    # Ollama uses no API key; the validator just no-ops for now.
    validator.validate_key(find("ollama"), "")


def test_openai_and_google_are_unchecked_until_their_sdks_are_wired():
    # Key-requiring providers without a validator yet must pass through —
    # otherwise the picker would block users from choosing them.
    validator.validate_key(find("openai"), "sk-anything")
    validator.validate_key(find("google"), "key-anything")


def test_anthropic_without_sdk_raises_helpful_error(monkeypatch):
    """If the anthropic SDK isn't installed, point the user at the extra.

    We make the lazy import fail by swapping sys.modules so the real
    ``import anthropic`` inside ``validate_key`` raises ImportError.
    """
    import sys

    real = sys.modules.get("anthropic")
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(validator.KeyValidationError) as exc:
        validator.validate_key(find("anthropic"), "sk-test")

    msg = str(exc.value).lower()
    assert "install" in msg and "anthropic" in msg

    if real is not None:
        monkeypatch.setitem(sys.modules, "anthropic", real)


def test_anthropic_rejects_key_when_sdk_signals_auth_error(monkeypatch):
    """A stubbed SDK that raises an auth error surfaces as KeyValidationError."""
    fake = _make_fake_anthropic(raise_error="auth")
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)

    with pytest.raises(validator.KeyValidationError):
        validator.validate_key(find("anthropic"), "sk-bad")


def test_anthropic_accepts_key_when_sdk_returns_normally(monkeypatch):
    fake = _make_fake_anthropic(raise_error=None)
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)

    # Must not raise.
    validator.validate_key(find("anthropic"), "sk-good")


def _make_fake_anthropic(*, raise_error):
    """Build a module-shaped stub with Anthropic client and AuthenticationError."""
    import types

    class AuthenticationError(Exception):
        pass

    class Messages:
        def create(self, **_kwargs):
            if raise_error == "auth":
                raise AuthenticationError("bad key")
            return types.SimpleNamespace(content=[types.SimpleNamespace(text="ok")])

    class Client:
        def __init__(self, *, api_key):
            self.api_key = api_key
            self.messages = Messages()

    module = types.ModuleType("anthropic")
    module.Anthropic = Client
    module.AuthenticationError = AuthenticationError
    return module
