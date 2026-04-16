"""Shared pytest fixtures.

Critically: *every* test runs with an in-memory fake for the keyring
backend. We never want a test run to read or write the developer's
actual OS credential manager — both because real stored keys would
make "no key saved" assertions flaky, and because CI shouldn't be
poking at the host keychain. Tests that specifically exercise
``keyring_store`` installs its own fake with higher priority.
"""

import pytest

from src import keyring_store


class _InMemoryKeyring:
    class errors:  # noqa: N801
        class PasswordDeleteError(Exception):
            pass

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        try:
            del self._store[(service, username)]
        except KeyError as e:
            raise self.errors.PasswordDeleteError from e


@pytest.fixture(autouse=True)
def _isolated_keyring(monkeypatch):
    """Give each test a fresh in-memory keyring so stored keys don't
    leak between tests or from the real OS credential manager."""
    monkeypatch.setattr(keyring_store, "_keyring", _InMemoryKeyring())


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    """The REPL tries to open the provider's key-creation page in the
    user's default browser. In tests this would pop hundreds of tabs
    during a full suite run. Swap it for a no-op that just records the
    URLs so individual tests can still assert on them."""
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(
        webbrowser, "open", lambda url, *_a, **_kw: opened.append(url) or True
    )


@pytest.fixture(autouse=True)
def _no_real_pdf_viewer(monkeypatch):
    """After a successful render, ``render_book_tool`` hands the A5 PDF
    to the OS's default viewer. A full suite run would therefore spawn
    a viewer per render — on Windows that means a burst of PDF windows
    during test collection. Swap the opener for a no-op; individual
    tests that want to assert the viewer was called inject their own
    ``open_file=`` instead of relying on the module-level default."""
    from src import agent_tools

    monkeypatch.setattr(agent_tools, "open_in_default_viewer", lambda _p: None)
