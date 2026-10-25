"""Tests for src/session_log.py — the per-run log under .book-gen/logs/
that lets a past session be inspected after the fact."""

from __future__ import annotations

import io
import logging

import pytest
from rich.console import Console

from src import session_log
from src.agent import Agent, AgentResponse, Tool


@pytest.fixture(autouse=True)
def _reset_logger():
    yield
    for handler in list(session_log.LOGGER.handlers):
        handler.close()
        session_log.LOGGER.removeHandler(handler)


def _read_log(path) -> str:
    for handler in session_log.LOGGER.handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_start_creates_a_log_file_under_book_gen_logs(tmp_path):
    path = session_log.start(tmp_path)

    session_log.LOGGER.info("hello")

    assert path.parent == tmp_path / ".book-gen" / "logs"
    assert "hello" in _read_log(path)


def test_start_keeps_only_the_most_recent_logs(tmp_path):
    logs = tmp_path / ".book-gen" / "logs"
    logs.mkdir(parents=True)
    for i in range(25):
        (logs / f"session-20260101-0000{i:02d}.log").write_text("old")

    path = session_log.start(tmp_path)

    remaining = sorted(logs.glob("session-*.log"))
    assert len(remaining) == session_log.KEEP
    assert path in remaining
    assert not (logs / "session-20260101-000000.log").exists()


def test_agent_turns_and_tool_calls_are_logged(tmp_path):
    path = session_log.start(tmp_path)

    class _LLM:
        def __init__(self):
            self.responses = [
                AgentResponse(
                    content=[
                        {"type": "text", "text": "Kapak üretiyorum"},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "generate_cover_illustration",
                            "input": {"prompt": "yavru kediler"},
                        },
                    ],
                    stop_reason="tool_use",
                ),
                AgentResponse(
                    content=[{"type": "text", "text": "bitti"}],
                    stop_reason="end_turn",
                ),
            ]

        def turn(self, _messages, _tools):
            if not self.responses:  # the agent's one-off "keep going" nudge
                return AgentResponse(content=[], stop_reason="end_turn")
            return self.responses.pop(0)

    tool = Tool(
        "generate_cover_illustration",
        "",
        {"type": "object"},
        lambda _i: "Cover generation failed: timeout",
    )
    console = Console(file=io.StringIO(), force_terminal=False)
    Agent(llm=_LLM(), tools=[tool], console=console).say("kapak yap")

    log = _read_log(path)
    assert "user: kapak yap" in log
    assert "assistant: Kapak üretiyorum" in log
    assert 'tool_use generate_cover_illustration {"prompt": "yavru kediler"}' in log
    assert "tool_result generate_cover_illustration: Cover generation failed" in log
    assert "assistant: bitti" in log


def test_nothing_is_written_when_no_session_log_was_started(tmp_path, caplog):
    """Library / test use without ``start`` must not create files."""
    with caplog.at_level(logging.INFO, logger="littlepress"):
        session_log.LOGGER.info("quiet")

    assert not (tmp_path / ".book-gen").exists()


def test_repl_logs_typed_lines_but_never_secrets(tmp_path):
    from src.repl import Repl

    path = session_log.start(tmp_path)
    lines = iter(["Yavru Kedi"])
    repl = Repl(
        read_line=lambda: next(lines),
        read_secret=lambda: "sk-super-secret",
        console=Console(file=io.StringIO(), force_terminal=False),
        session_root=tmp_path,
    )

    assert repl._read() == "Yavru Kedi"
    assert repl._read_secret() == "sk-super-secret"

    log = _read_log(path)
    assert "input: Yavru Kedi" in log
    assert "sk-super-secret" not in log


def test_cli_main_starts_a_session_log(tmp_path, monkeypatch):
    from src import cli

    monkeypatch.chdir(tmp_path)

    def eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main([]) == 0

    logs = list((tmp_path / ".book-gen" / "logs").glob("session-*.log"))
    assert len(logs) == 1
    assert "littlepress" in _read_log(logs[0])


def test_cli_main_logs_a_crash_with_its_traceback(tmp_path, monkeypatch):
    from src import cli
    from src.repl import Repl

    monkeypatch.chdir(tmp_path)

    def boom(_self):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(Repl, "run", boom)
    with pytest.raises(RuntimeError):
        cli.main([])

    log = _read_log(next((tmp_path / ".book-gen" / "logs").glob("session-*.log")))
    assert "crashed" in log
    assert "RuntimeError: kaboom" in log
