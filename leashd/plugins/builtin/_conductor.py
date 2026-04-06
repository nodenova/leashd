"""Conductor — AI-driven orchestration decisions for the agentic task loop.

The conductor is a one-shot ``claude -p`` call that decides the next action
for the coding agent.  It replaces both the fixed ``_build_phase_pipeline()``
and the ``_cli_evaluator.evaluate_phase_outcome()`` with a single,
context-aware decision point.
"""

import json
import re
from typing import Literal, get_args

import structlog
from pydantic import BaseModel, ConfigDict

from leashd.plugins.builtin._cli_evaluator import evaluate_via_cli, sanitize_for_prompt

logger = structlog.get_logger()

ConductorAction = Literal[
    "explore",
    "plan",
    "implement",
    "test",
    "verify",
    "fix",
    "review",
    "pr",
    "complete",
    "escalate",
]

_VALID_ACTIONS: frozenset[str] = frozenset(get_args(ConductorAction))

ConductorComplexity = Literal["trivial", "simple", "moderate", "complex", "critical"]

_VALID_COMPLEXITIES: frozenset[str] = frozenset(get_args(ConductorComplexity))


class ConductorDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ConductorAction
    reason: str = ""
    instruction: str = ""
    complexity: ConductorComplexity | None = None


_CONDUCTOR_SYSTEM_PROMPT = """\
You are the orchestrator for an autonomous coding agent. You receive the task \
description, the agent's working memory file, and the output of the last action. \
Your job is to decide the SINGLE NEXT ACTION the coding agent should take.

Available actions:
- EXPLORE: Read codebase to understand architecture, conventions, and context.
- PLAN: Create a detailed implementation plan for complex changes.
- IMPLEMENT: Write code changes following the plan or task description.
- TEST: Run automated test suites (pytest, jest, vitest, etc.).
- VERIFY: Browser-based verification — start dev server, navigate to pages/endpoints, \
confirm UI renders correctly or API responds as expected. Use for FE/BE work. \
The coding agent has pre-configured browser tools (Playwright MCP or agent-browser CLI) \
for this action.
- FIX: Fix specific issues found in testing or verification.
- REVIEW: Self-review all changes via git diff. Read-only — no modifications.
- PR: Create a pull request (branch, commit, push, gh pr create).
- COMPLETE: Task is fully done and verified.
- ESCALATE: Human intervention needed — stuck, ambiguous, or beyond agent capability.

Complexity levels (assess on first call only):
- TRIVIAL: Single-line fix, simple query, config tweak
- SIMPLE: Small bug fix, config change, minor feature (<50 lines)
- MODERATE: Multi-file change, new feature, requires architecture understanding
- COMPLEX: Major refactor, new subsystem, cross-cutting concerns
- CRITICAL: Security fix, data migration, breaking change

Typical flows (guidelines, not rules):
- TRIVIAL: implement → complete
- SIMPLE: explore → implement → test → complete
- MODERATE: explore → plan → implement → test → verify → review → complete
- COMPLEX: explore → plan → implement → test → verify → fix → review → pr → complete

Rules:
- TEST is mandatory before COMPLETE for any task that modifies code (only TRIVIAL \
config tweaks or queries may skip it)
- VERIFY (browser) is mandatory for any task involving UI components, CSS/styling, \
web endpoints, API changes, or frontend work — run the dev server and visually confirm \
the result. TEST alone is not sufficient for visual changes.
- Always REVIEW before COMPLETE on non-trivial tasks
- If tests/verification failed 3+ times for the same reason → ESCALATE
- If the memory file shows prior work, continue from the checkpoint — don't restart
- Skip EXPLORE if the task is self-contained and trivial
- Skip PLAN if the task is simple enough to implement directly
- When uncertain whether to retry or escalate, check the retry count
- Never go directly from IMPLEMENT to COMPLETE — always TEST first

Respond with EXACTLY one JSON object (no markdown fences, no extra text):
{"action": "<ACTION>", "reason": "<one-line why>", "instruction": "<specific guidance \
for the coding agent>"}

On the FIRST call (when complexity has not been assessed yet), also include:
{"action": "...", "reason": "...", "instruction": "...", "complexity": "<LEVEL>"}\
"""

_FIRST_CALL_ADDENDUM = """
This is a NEW task — no prior work has been done. Assess its complexity and \
decide the first action. Include the "complexity" field in your response."""


def _build_conductor_context(
    *,
    task_description: str,
    memory_content: str | None,
    last_output: str,
    current_phase: str,
    retry_count: int,
    max_retries: int,
    is_first_call: bool,
) -> str:
    parts: list[str] = [f"TASK: {task_description}"]

    if is_first_call:
        parts.append(_FIRST_CALL_ADDENDUM)
    else:
        parts.append(f"\nCURRENT PHASE: {current_phase}")
        parts.append(f"RETRIES: {retry_count} of {max_retries}")

    if memory_content:
        sanitized = sanitize_for_prompt(memory_content)
        parts.append(f"\nMEMORY FILE:\n<<<\n{sanitized}\n>>>")

    if last_output:
        sanitized_output = sanitize_for_prompt(last_output[:4000])
        parts.append(f"\nLAST ACTION OUTPUT:\n<<<\n{sanitized_output}\n>>>")

    return "\n".join(parts)


_JSON_RE = re.compile(r"\{(?:[^{}]|\{[^{}]*\})*\}")
_FALLBACK_RE = re.compile(
    rf"^({'|'.join(_VALID_ACTIONS)})\s*:\s*(.+)$",
    re.IGNORECASE,
)


def _parse_response(raw: str) -> ConductorDecision:
    """Parse conductor response — try JSON first, fall back to ACTION: reason."""
    raw = raw.strip()

    # Try JSON
    match = _JSON_RE.search(raw)
    if match:
        try:
            data = json.loads(match.group())
            action = str(data.get("action", "")).lower()
            if action in _VALID_ACTIONS:
                complexity = data.get("complexity")
                if complexity and str(complexity).lower() not in _VALID_COMPLEXITIES:
                    complexity = None
                return ConductorDecision(
                    action=action,  # type: ignore[arg-type]
                    reason=str(data.get("reason", "")),
                    instruction=str(data.get("instruction", "")),
                    complexity=str(complexity).lower() if complexity else None,  # type: ignore[arg-type]
                )
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    # Fallback: ACTION: reason
    first_line = raw.split("\n")[0] if raw else ""
    fb_match = _FALLBACK_RE.match(first_line)
    if fb_match:
        action = fb_match.group(1).lower()
        if action in _VALID_ACTIONS:
            return ConductorDecision(
                action=action,  # type: ignore[arg-type]
                reason=fb_match.group(2).strip(),
            )

    # Default: advance to implement (fail-forward)
    logger.warning("conductor_parse_failed", raw=raw[:200])
    return ConductorDecision(
        action="implement",
        reason="unparseable conductor response — defaulting to implement",
    )


async def decide_next_action(
    *,
    task_description: str,
    memory_content: str | None,
    last_output: str,
    current_phase: str,
    retry_count: int = 0,
    max_retries: int = 3,
    is_first_call: bool = False,
    model: str | None = None,
    timeout: float = 45.0,
) -> ConductorDecision:
    """Ask the conductor what the coding agent should do next.

    On CLI/timeout errors, returns a fallback decision rather than raising.
    """
    context = _build_conductor_context(
        task_description=task_description,
        memory_content=memory_content,
        last_output=last_output,
        current_phase=current_phase,
        retry_count=retry_count,
        max_retries=max_retries,
        is_first_call=is_first_call,
    )

    try:
        raw = await evaluate_via_cli(
            _CONDUCTOR_SYSTEM_PROMPT,
            context,
            model=model,
            timeout=timeout,
        )
    except (TimeoutError, RuntimeError) as exc:
        exc_detail = str(exc) or f"{type(exc).__name__} (no details)"
        is_timeout = isinstance(exc, TimeoutError)
        logger.warning(
            "conductor_call_failed",
            error=exc_detail,
            kind="timeout" if is_timeout else "cli_error",
        )
        # Fail-forward: if we haven't started, explore; otherwise implement
        fallback_action: ConductorAction = "explore" if is_first_call else "implement"
        reason_prefix = "conductor timed out" if is_timeout else "conductor call failed"
        return ConductorDecision(
            action=fallback_action,
            reason=f"{reason_prefix}: {exc_detail}",
            instruction="Proceed with the task based on available context.",
        )

    decision = _parse_response(raw)
    logger.info(
        "conductor_decision",
        action=decision.action,
        reason=decision.reason,
        complexity=decision.complexity,
    )
    return decision
