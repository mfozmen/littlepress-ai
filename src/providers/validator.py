"""Verify that a freshly-entered API key actually authenticates.

The validator is provider-aware: each implementation knows the cheapest
ping it can send to the real service so the REPL can reject a mistyped
key immediately, before the user invests any more effort.

SDK imports are lazy on purpose — users who only use the offline or
Ollama provider should not need to install third-party SDKs.
"""

from __future__ import annotations

from src.providers.llm import ProviderSpec


class KeyValidationError(Exception):
    """Raised when the supplied API key cannot authenticate with the provider."""


def validate_key(spec: ProviderSpec, api_key: str) -> None:
    """Raise ``KeyValidationError`` if ``api_key`` isn't usable for ``spec``.

    Providers without a validation path yet (OpenAI, Google) and key-less
    providers (none, ollama) currently no-op. They will grow real checks
    as their SDK wiring lands.
    """
    if not spec.requires_api_key:
        return
    checker = _CHECKERS.get(spec.name, _unchecked)
    checker(api_key)


def _unchecked(_api_key: str) -> None:
    """Placeholder for providers that haven't gotten a ping yet."""
    return None


def _check_anthropic(api_key: str) -> None:
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError as e:
        raise KeyValidationError(
            "The 'anthropic' SDK isn't installed. Run: "
            "pip install 'child-book-generator[anthropic]'"
        ) from e

    try:
        client = anthropic.Anthropic(api_key=api_key)
        # Cheapest possible call that still exercises auth. If the key is
        # bad, the SDK raises AuthenticationError before we finish the
        # request.
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except getattr(anthropic, "AuthenticationError", Exception) as e:
        raise KeyValidationError(f"Anthropic rejected the key: {e}") from e


_CHECKERS = {
    "anthropic": _check_anthropic,
}
