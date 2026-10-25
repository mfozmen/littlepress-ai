"""Agent loop — drive a tool-use conversation with an LLM.

The Agent is deliberately small. It owns:

- The conversation history (messages).
- A tool registry (name → handler).
- One method ``say(text)`` that advances the loop until the LLM stops
  requesting tools.

Tool results are fed back verbatim; preserve-child-voice is enforced at
the *tool surface* — the agent never has a tool that rewrites page text.
Typo/OCR fixes are proposed tools that must surface to the user.

Format follows Anthropic's tool-use wire shape (content blocks with
``type`` = ``text`` / ``tool_use`` / ``tool_result``). Other providers
translate in their adapter.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from src.metadata_i18n import detect_lang, t
from src.session_log import LOGGER


_NUDGE = (
    "(automatic note, not from the user) Your last reply ended without a "
    "tool call or a question, so nothing is happening. If you said you "
    "would do something, do it now with tool calls. If you need the "
    "user, ask them directly. If everything is finished, reply with an "
    "empty message."
)


@dataclass
class Tool:
    """A callable the agent can ask the LLM to invoke.

    ``input_schema`` is a JSON Schema object describing the ``input`` dict
    the handler receives. ``handler`` returns a plain-text result the
    agent forwards to the LLM untouched.
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]


@dataclass
class AgentResponse:
    """Shape returned by ``LLMProvider.turn``.

    ``content`` is a list of content blocks in Anthropic's format:
    ``{"type": "text", "text": ...}`` or
    ``{"type": "tool_use", "id": ..., "name": ..., "input": ...}``.
    """

    content: list[dict]
    stop_reason: str  # "end_turn" | "tool_use" (| others we don't handle yet)


class Agent:
    def __init__(
        self,
        llm,
        tools: list[Tool],
        console: Console,
    ) -> None:
        self._llm = llm
        self._tools: dict[str, Tool] = {t.name: t for t in tools}
        self._tool_list: list[Tool] = list(tools)
        self._console = console
        self._messages: list[dict] = []
        # Live indicator shown while the LLM or a tool works; ``None`` when idle.
        self._progress: Progress | None = None

    def say(self, user_text: str) -> None:
        """Append a user message and drive the loop until the LLM stops."""
        LOGGER.info("user: %s", user_text)
        self._messages.append({"role": "user", "content": user_text})
        self._drive()

    def _drive(self) -> None:
        used_tools = nudged = False
        while True:
            with self.working(t("progress.thinking", detect_lang())):
                response = self._llm.turn(self._messages, self._tool_list)
            self._messages.append({"role": "assistant", "content": response.content})
            text = ""
            for block in response.content:
                if block.get("type") == "text" and block["text"].strip():
                    LOGGER.info("assistant: %s", block["text"])
                    self._console.print(block["text"])
                    text += block["text"]
            if response.stop_reason != "tool_use":
                # Some models announce the next step ("I'll render now")
                # and stop, leaving the user at an idle prompt.
                if used_tools and not nudged and "?" not in text:
                    nudged = True
                    LOGGER.info("nudge: reply ended without a tool call or question")
                    self._messages.append({"role": "user", "content": _NUDGE})
                    continue
                return
            used_tools = True
            tool_results: list[dict] = []
            for block in response.content:
                if block.get("type") != "tool_use":
                    continue
                lang = detect_lang()
                label = t(f"progress.{block['name']}", lang) or t(
                    "progress.working", lang
                )
                with self.working(label):
                    content = self._run_tool(block["name"], block.get("input", {}))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": content,
                    }
                )
            self._messages.append({"role": "user", "content": tool_results})

    @contextmanager
    def working(self, label: str) -> Iterator[None]:
        """Show a spinner, a pulsing bar and the elapsed time until the
        block exits. There is no real percentage to show, so the bar is
        indeterminate; it is erased when the work finishes."""
        # The braille spinner can't be encoded on a cp125x stream (piped
        # output on Windows); rich already makes the bar ASCII there.
        utf = self._console.encoding.lower().startswith("utf")
        progress = Progress(
            SpinnerColumn("dots" if utf else "line"),
            TextColumn("{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
        )
        progress.add_task(label, total=None)
        self._progress = progress
        try:
            with progress:
                yield
        finally:
            self._progress = None

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Hide the live indicator while the user is asked something, so
        the prompt and their typing don't collide with the animation."""
        progress = self._progress
        if progress is None:
            yield
            return
        progress.stop()
        try:
            yield
        finally:
            progress.start()

    def _run_tool(self, name: str, input_: dict) -> str:
        LOGGER.info(
            "tool_use %s %s", name, json.dumps(input_, ensure_ascii=False, default=str)
        )
        result = self._call_tool(name, input_)
        LOGGER.info("tool_result %s: %s", name, result)
        return result

    def _call_tool(self, name: str, input_: dict) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: no such tool: {name}"
        try:
            return tool.handler(input_)
        except Exception as e:
            LOGGER.exception("tool %s raised", name)
            return f"Error: {e}"
