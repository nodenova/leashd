"""Shared utilities for connectors — text splitting, retry, text-based fallbacks."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Literal, TypeVar

import structlog

from leashd.exceptions import ConnectorError

logger = structlog.get_logger()

_T = TypeVar("_T")

# --- Callback data prefixes (shared across all connectors) ---

APPROVAL_PREFIX = "approval:"
INTERACTION_PREFIX = "interact:"
INTERRUPT_PREFIX = "interrupt:"
GIT_PREFIX = "git:"
DIR_PREFIX = "dir:"
WS_PREFIX = "ws:"

# --- Tool classification for activity labels ---

_SEARCH_TOOLS = frozenset(
    {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "TaskGet", "TaskList"}
)
_EDIT_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})
_THINK_TOOLS = frozenset(
    {
        "EnterPlanMode",
        "ExitPlanMode",
        "plan",
        "AskUserQuestion",
        "TodoWrite",
        "TaskCreate",
        "TaskUpdate",
    }
)
_BASH_SEARCH_RE = re.compile(
    r"^(ls|cat|head|tail|find|grep|rg|wc|du|df|pwd|echo|date|whoami|which|type|file|stat|tree)\b"
)
_BASH_GIT_READ_RE = re.compile(
    r"^git\s+(.+\s+)?(status|log|diff|show|branch|remote|tag)\b"
)

# --- Plan review option mapping (number → callback value) ---

_PLAN_REVIEW_OPTIONS = [
    ("1", "clean_edit", "Yes, clear context and auto-accept edits"),
    ("2", "edit", "Yes, auto-accept edits"),
    ("3", "default", "Yes, manually approve edits"),
    ("4", "adjust", "Adjust the plan"),
]


def split_text(text: str, max_length: int = 4000) -> list[str]:
    """Split text into chunks that fit within *max_length*.

    Splits on newlines first, then spaces, then hard-breaks.
    """
    if not text:
        return [""]
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        split_at = text.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = text.rfind(" ", 0, max_length)
        if split_at <= 0:
            split_at = max_length

        chunks.append(text[:split_at])
        text = text[split_at + 1 :] if split_at < max_length else text[split_at:]

    return chunks


async def retry_on_error(
    factory: Callable[[], Coroutine[object, object, _T]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    retryable: tuple[type[Exception], ...] = (Exception,),
    operation: str = "",
) -> _T:
    """Retry *factory()* on transient errors with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await factory()
        except retryable as exc:
            delay = min(base_delay * (2**attempt), max_delay)
            last_exc = exc
            logger.warning(
                "connector_retry",
                operation=operation,
                attempt=attempt + 1,
                max_retries=max_retries,
                delay=delay,
                error=str(exc),
            )
            await asyncio.sleep(delay)

    raise ConnectorError(f"{operation} failed after {max_retries} retries: {last_exc}")


def activity_label(tool_name: str, description: str = "") -> tuple[str, str]:
    """Return (emoji, verb) for a tool's activity message."""
    if tool_name == "Bash":
        if _BASH_SEARCH_RE.search(description) or _BASH_GIT_READ_RE.search(description):
            return ("🔍", "Searching")
        return ("⚡", "Running")
    if tool_name in _EDIT_TOOLS:
        return ("✏️", "Editing")
    if tool_name in _SEARCH_TOOLS:
        return ("🔍", "Searching")
    if tool_name in _THINK_TOOLS:
        return ("🧠", "Thinking")
    if tool_name.startswith(("mcp__playwright__", "browser_")):
        return ("🌐", "Browsing")
    if tool_name == "Agent":
        lowered = description.lower()
        if any(w in lowered for w in ("plan", "design", "architect")):
            return ("🧠", "Thinking")
        return ("🔍", "Searching")
    return ("⏳", "Running")


def format_text_approval(description: str) -> str:
    """Format an approval request for text-only platforms (no buttons)."""
    return (
        f"⚠️ APPROVAL REQUIRED\n\n"
        f"{description}\n\n"
        f"Reply: approve, reject, or approve-all"
    )


def format_text_question(
    question_text: str,
    header: str,
    options: list[dict[str, str]],
) -> str:
    """Format a question with numbered options for text-only platforms."""
    lines = []
    if header:
        lines.append(f"❓ {header}")
    lines.append(question_text)
    lines.append("")
    for i, opt in enumerate(options, 1):
        label = opt.get("label", "")
        lines.append(f"{i}. {label}")
    lines.append("")
    lines.append("Reply with a number, or type a custom answer.")
    return "\n".join(lines)


def format_text_plan_review(description: str) -> str:
    """Format a plan review prompt with numbered choices for text-only platforms."""
    truncated = description[:4000]
    if len(description) > 4000:
        truncated += "\n\n... (truncated)"

    lines = [truncated, "", "Proceed with implementation?", ""]
    for num, _value, label in _PLAN_REVIEW_OPTIONS:
        lines.append(f"{num}. {label}")
    lines.append("")
    lines.append("Reply with a number (1-4).")
    return "\n".join(lines)


@dataclass(frozen=True)
class ParsedResponse:
    """Result of parsing a text reply against pending connector state."""

    kind: Literal["approval", "interaction", "plan_review"]
    value: str


def parse_text_response(
    text: str,
    *,
    has_pending_approval: bool = False,
    has_pending_interaction: bool = False,
    pending_options: list[dict[str, str]] | None = None,
    has_pending_plan_review: bool = False,
) -> ParsedResponse | None:
    """Parse a user's text reply against pending approval/interaction/plan state.

    Returns None if the text doesn't match any pending state.
    """
    stripped = text.strip().lower()

    if has_pending_approval:
        if stripped in ("approve", "yes", "y"):
            return ParsedResponse(kind="approval", value="approve")
        if stripped in ("reject", "no", "n"):
            return ParsedResponse(kind="approval", value="reject")
        if stripped in ("approve-all", "all"):
            return ParsedResponse(kind="approval", value="approve-all")

    if has_pending_plan_review:
        for num, value, _label in _PLAN_REVIEW_OPTIONS:
            if stripped == num:
                return ParsedResponse(kind="plan_review", value=value)

    if has_pending_interaction and pending_options:
        try:
            idx = int(stripped)
            if 1 <= idx <= len(pending_options):
                label = pending_options[idx - 1].get("label", "")
                return ParsedResponse(kind="interaction", value=label)
        except ValueError:
            pass

    return None
