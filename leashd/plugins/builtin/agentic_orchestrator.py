"""Agentic task orchestrator (v2) — conductor-driven autonomous workflows.

Replaces the fixed-pipeline ``TaskOrchestrator`` with an LLM-driven
conductor loop.  Instead of a predetermined phase sequence, a one-shot
Claude CLI call (the *conductor*) decides what the coding agent should
do next based on the task description, a persistent memory file, and
the output of the last action.

The conductor loop::

    seed memory file
           │
    ┌──────▼──────────────────────────────────┐
    │  CONDUCTOR  (one-shot claude -p call)    │
    │  reads: task + memory file + last output │
    │  returns: {action, reason, instruction}  │
    └──────┬──────────────────────────────────┘
           │
    DISPATCH to coding agent (explore / plan / implement / …)
           │
    SESSION_COMPLETED → capture output → loop back to CONDUCTOR
           │
    until COMPLETE or ESCALATE

Each task gets a ``.leashd/tasks/{run_id}.md`` working-memory file
that the coding agent updates during execution.  The conductor reads
it to maintain context across actions and daemon restarts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog

from leashd.core import task_memory
from leashd.core.context_manager import (
    git_checkpoint,
    mask_phase_output,
    summarize_phase_output,
)
from leashd.core.events import (
    CONFIG_RELOADED,
    MESSAGE_IN,
    SESSION_COMPLETED,
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_ESCALATED,
    TASK_FAILED,
    TASK_PHASE_CHANGED,
    TASK_RESUMED,
    TASK_SUBMITTED,
    Event,
)
from leashd.core.queue import KeyedAsyncQueue
from leashd.core.task import TaskOutcome, TaskRun, TaskStore
from leashd.plugins.base import LeashdPlugin, PluginMeta
from leashd.plugins.builtin._conductor import ConductorDecision, decide_next_action
from leashd.plugins.builtin.browser_tools import (
    AGENT_BROWSER_AUTO_APPROVE,
    BROWSER_MUTATION_TOOLS,
    BROWSER_READONLY_TOOLS,
)
from leashd.plugins.builtin.task_orchestrator import IMPLEMENT_BASH_AUTO_APPROVE
from leashd.plugins.builtin.test_config_loader import (
    discover_api_specs,
    load_project_test_config,
)
from leashd.plugins.builtin.test_runner import (
    TEST_BASH_AUTO_APPROVE,
    TestConfig,
    build_test_instruction,
    merge_project_config,
    read_test_session_context,
)

if TYPE_CHECKING:
    from typing import Protocol

    from leashd.connectors.base import BaseConnector
    from leashd.core.events import EventBus
    from leashd.plugins.base import PluginContext

    class _EngineProtocol(Protocol):
        session_manager: Any
        agent: Any

        async def handle_message(
            self, user_id: str, text: str, chat_id: str, attachments: Any = None
        ) -> str: ...

        def enable_tool_auto_approve(self, chat_id: str, tool_name: str) -> None: ...

        def disable_auto_approve(self, chat_id: str) -> None: ...

        def get_executing_session_id(self, chat_id: str) -> str | None: ...


logger = structlog.get_logger()

_STALE_TASK_HOURS = 24

# ── Action → session mode mapping ──────────────────────────────────────

_ACTION_TO_MODE: dict[str, str] = {
    "explore": "auto",
    "plan": "plan",
    "implement": "auto",
    "test": "test",
    "verify": "test",
    "fix": "auto",
    "review": "plan",
    "pr": "auto",
}

# ── Read-only tools for explore/review ─────────────────────────────────

_READ_ONLY_BASH: frozenset[str] = frozenset(
    {
        "Bash::cat",
        "Bash::ls",
        "Bash::head",
        "Bash::tail",
        "Bash::wc",
        "Bash::grep",
        "Bash::find",
    }
)

# ── Action-specific prompt suffixes ────────────────────────────────────

_ACTION_SUFFIXES: dict[str, str] = {
    "explore": (
        "Focus on understanding the codebase. Read relevant files, search for "
        "patterns, check existing tests and conventions. Read CLAUDE.md if "
        "present — it contains project-specific commands and conventions.\n\n"
        "BEFORE YOU FINISH this action, update the task memory file:\n"
        "- Write your findings to ## Codebase Context\n"
        "- Add a row to the ## Progress table\n"
        "- Update ## Checkpoint"
    ),
    "plan": (
        "Read CLAUDE.md and any existing documentation first. Create a detailed "
        "implementation plan covering:\n"
        "- Files to create or modify\n"
        "- Key changes in each file\n"
        "- Test strategy\n\n"
        "BEFORE YOU FINISH this action, update the task memory file:\n"
        "- Write the plan to ## Plan\n"
        "- Add a row to the ## Progress table\n"
        "- Update ## Checkpoint"
    ),
    "implement": (
        "Implement the changes described in the instruction. Work file by file, "
        "writing clean code that follows existing conventions.\n\n"
        "MANDATORY VERIFICATION — use the project's own commands (from CLAUDE.md, "
        "Makefile, or package.json), NOT generic commands like 'npx lint':\n"
        "1. Run the project's lint/format checks\n"
        "2. Run the project's type checks (if applicable)\n"
        "3. Run the project's unit tests\n"
        "Fix ALL failures before completing this phase.\n\n"
        "BEFORE YOU FINISH this action, update the task memory file:\n"
        "- Write changed files and a summary to ## Changes\n"
        "- Add a row to the ## Progress table\n"
        "- Update ## Checkpoint"
    ),
    "test": (
        "Run automated test suites to verify the implementation. Check CLAUDE.md "
        "or package.json for the project's test commands.\n\n"
        "BEFORE YOU FINISH this action, update the task memory file:\n"
        "- Write results to ## Test Results\n"
        "- Add a row to the ## Progress table\n"
        "- Update ## Checkpoint"
    ),
    "fix": (
        "Fix the specific issues described in the instruction. Focus only on "
        "the failures — don't refactor unrelated code. Use the project's own "
        "verification commands (from CLAUDE.md, Makefile, or package.json).\n\n"
        "BEFORE YOU FINISH this action, update the task memory file:\n"
        "- Write what you fixed to ## Changes\n"
        "- Add a row to the ## Progress table\n"
        "- Update ## Checkpoint"
    ),
    "review": (
        "Self-review ALL changes made during this task:\n"
        "1. Run `git diff` to see the full changeset\n"
        "2. For each changed file, check:\n"
        "   - Does the code follow existing patterns and conventions?\n"
        "   - Are there edge cases or error conditions not handled?\n"
        "   - Any security concerns (hardcoded secrets, SQL injection, etc.)?\n"
        "   - Is the code clean (no debug prints, no TODOs, no commented-out "
        "code)?\n"
        "   - Does it match the original task requirements?\n\n"
        "Do NOT make any changes — only review and report.\n\n"
        "BEFORE YOU FINISH this action, update the task memory file:\n"
        "- Write your review to ## Review Notes\n"
        "- Add a row to the ## Progress table\n"
        "- Update ## Checkpoint"
    ),
    "pr": (
        "All tests pass. Create a pull request for the changes:\n"
        "1. Check `git status` and `git diff`\n"
        "2. Create a new branch from HEAD if not already on a feature branch\n"
        "3. Stage and commit all changes with a descriptive commit message\n"
        "4. Push the branch to origin\n"
        "5. Create a PR using `gh pr create`\n\n"
        "Keep the PR title short and the body concise.\n\n"
        "BEFORE YOU FINISH this action, update the task memory file:\n"
        "- Add a row to the ## Progress table\n"
        "- Update ## Checkpoint"
    ),
}


def _verify_suffix(browser_backend: str) -> str:
    """Build verify action suffix with backend-specific tool names."""
    if browser_backend == "agent-browser":
        return (
            "Browser-based verification — this is manual-style checking, not "
            "automated test suites.\n\n"
            "1. Start the dev server if it's not already running\n"
            "2. Use agent-browser CLI to navigate to relevant pages/endpoints "
            "(agent-browser open <url>)\n"
            "3. Take snapshots with `agent-browser snapshot -i` to inspect the "
            "accessibility tree\n"
            "4. Check `agent-browser console` for JavaScript errors\n"
            "5. Take screenshots with `agent-browser screenshot` if useful\n"
            "6. Verify the UI renders correctly, forms work, API responds as "
            "expected\n"
            "7. Check error states and edge cases in the browser\n\n"
            "Write your findings to the ## Verification section of the task "
            "memory file. Update ## Progress and ## Checkpoint."
        )
    return (
        "Browser-based verification — this is manual-style checking, not "
        "automated test suites.\n\n"
        "1. Start the dev server if it's not already running\n"
        "2. Use browser_navigate to go to relevant pages/endpoints\n"
        "3. Take browser_snapshot to inspect the accessibility tree\n"
        "4. Check browser_console_messages for JavaScript errors\n"
        "5. Take browser_take_screenshot if useful for documenting behavior\n"
        "6. Verify the UI renders correctly, forms work, API responds as "
        "expected\n"
        "7. Check error states and edge cases in the browser\n\n"
        "Write your findings to the ## Verification section of the task "
        "memory file. Update ## Progress and ## Checkpoint."
    )


def _build_action_prompt(
    task: TaskRun,
    decision: ConductorDecision,
    memory_content: str | None,
    browser_backend: str = "playwright",
) -> str:
    """Build the prompt sent to the coding agent for a given action."""
    lines = [
        f"AUTONOMOUS TASK — Action: {decision.action}",
        f"TASK: {task.task}",
        f"WORKING DIRECTORY: {task.working_directory}",
        "",
        f"INSTRUCTION: {decision.instruction}",
        "",
    ]

    if memory_content:
        lines.append(
            f"TASK MEMORY (.leashd/tasks/{task.run_id}.md):\n```\n{memory_content}\n```"
        )
        lines.append("")
        lines.append(
            "MANDATORY: Before finishing, update the task memory file above. "
            "Fill in the relevant content sections (## Codebase Context, "
            "## Plan, ## Changes, etc.) as directed below — not just "
            "## Checkpoint."
        )
    else:
        lines.append(
            f"Create and maintain .leashd/tasks/{task.run_id}.md with your "
            "findings, changes, and progress."
        )

    lines.append("")
    if decision.action == "verify":
        suffix = _verify_suffix(browser_backend)
    else:
        suffix = _ACTION_SUFFIXES.get(decision.action, "Continue the task.")
    lines.append(suffix)

    # Include checkpoint history for context
    checkpoints = [
        (k.replace("_checkpoint", ""), v)
        for k, v in task.phase_context.items()
        if k.endswith("_checkpoint") and isinstance(v, str)
    ]
    if checkpoints:
        lines.append("")
        lines.append("GIT CHECKPOINTS (auto-committed after each phase):")
        for phase_name, commit_hash in checkpoints:
            lines.append(f"  - {phase_name}: {commit_hash}")

    if decision.action == "pr":
        base = task.phase_context.get("auto_pr_base_branch", "main")
        lines.append(f"\nTarget branch: {base}")

    return "\n".join(lines)


class AgenticOrchestrator(LeashdPlugin):
    """Conductor-driven autonomous task orchestrator (v2)."""

    meta = PluginMeta(
        name="task_orchestrator",
        version="2.0.0",
        description="Agentic task orchestrator with conductor-driven action loop",
    )

    def __init__(
        self,
        task_store: TaskStore | None = None,
        connector: BaseConnector | None = None,
        *,
        db_path: str | None = None,
        max_retries: int = 3,
        auto_pr: bool = False,
        auto_pr_base_branch: str = "main",
        conductor_model: str | None = None,
        conductor_timeout: float = 45.0,
        memory_max_chars: int = 8000,
    ) -> None:
        self._store = task_store
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._connector = connector
        self._max_retries = max_retries
        self._auto_pr = auto_pr
        self._auto_pr_base_branch = auto_pr_base_branch
        self._conductor_model = conductor_model
        self._conductor_timeout = conductor_timeout
        self._memory_max_chars = memory_max_chars
        self._browser_backend: str = "playwright"
        self._active_tasks: dict[str, TaskRun] = {}
        self._queue = KeyedAsyncQueue()
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._engine: _EngineProtocol | None = None
        self._event_bus: EventBus | None = None
        self._subscriptions: list[tuple[str, Any]] = []

    @property
    def store(self) -> TaskStore:
        if self._store is None:
            raise RuntimeError("TaskStore not initialized — call start() first")
        return self._store

    def set_engine(self, engine: _EngineProtocol) -> None:
        self._engine = engine

    # ── Plugin lifecycle ───────────────────────────────────────────────

    async def initialize(self, context: PluginContext) -> None:
        self._event_bus = context.event_bus
        self._browser_backend = context.config.browser_backend
        self._subscriptions = [
            (TASK_SUBMITTED, self._on_task_submitted),
            (SESSION_COMPLETED, self._on_session_completed),
            (MESSAGE_IN, self._on_user_message),
            (CONFIG_RELOADED, self._on_config_reloaded),
        ]
        for event_name, handler in self._subscriptions:
            context.event_bus.subscribe(event_name, handler)

    async def start(self) -> None:
        if self._store is None and self._db_path:
            import aiosqlite as _aiosqlite

            self._db = await _aiosqlite.connect(self._db_path)
            self._db.row_factory = _aiosqlite.Row
            self._store = TaskStore(self._db)
            await self._store.create_tables()

        if self._store is None:
            logger.error("agentic_orchestrator_no_store")
            return

        stale_count = await self.cleanup_stale()
        if stale_count:
            logger.info("task_stale_cleaned_on_start", count=stale_count)

        active = await self.store.load_all_active()
        for task in active:
            self._active_tasks[task.chat_id] = task
            logger.info(
                "task_recovering",
                run_id=task.run_id,
                phase=task.phase,
                chat_id=task.chat_id,
            )
            await self._resume_task(task)
        if active:
            logger.info("task_recovery_complete", count=len(active))

    async def stop(self) -> None:
        if self._event_bus and self._subscriptions:
            for event_name, handler in self._subscriptions:
                self._event_bus.unsubscribe(event_name, handler)
        for t in self._running_tasks.values():
            t.cancel()
        self._running_tasks.clear()
        self._active_tasks.clear()
        if self._db:
            await self._db.close()
            self._db = None

    # ── Event handlers ─────────────────────────────────────────────────

    async def _on_task_submitted(self, event: Event) -> None:
        chat_id = event.data.get("chat_id", "")

        existing = self._active_tasks.get(chat_id)
        if existing and not existing.is_terminal():
            if self._connector:
                await self._connector.send_message(
                    chat_id,
                    f"A task is already running (action: {existing.phase}). "
                    "Send /cancel to stop it first.",
                )
            return

        task = TaskRun(
            user_id=event.data["user_id"],
            chat_id=chat_id,
            session_id=event.data["session_id"],
            task=event.data["task"],
            working_directory=event.data["working_directory"],
            max_retries=self._max_retries,
        )
        task.phase_context["auto_pr_base_branch"] = self._auto_pr_base_branch

        # Seed the memory file
        mem_path = task_memory.seed(task.run_id, task.task, task.working_directory)
        task.memory_file_path = str(mem_path)

        await self.store.save(task)
        self._active_tasks[chat_id] = task

        logger.info(
            "agentic_task_created",
            run_id=task.run_id,
            chat_id=chat_id,
            task_preview=task.task[:80],
        )

        await self._advance(task, is_first_call=True)

    async def _on_session_completed(self, event: Event) -> None:
        session = event.data.get("session")
        if not session:
            return

        chat_id = event.data.get("chat_id", getattr(session, "chat_id", ""))
        task = self._active_tasks.get(chat_id)
        if task is None or task.is_terminal():
            return

        task_run_id = getattr(session, "task_run_id", None)
        if task_run_id and task_run_id != task.run_id:
            return

        response_content = event.data.get("response_content", "")
        masked = mask_phase_output(response_content)
        task.phase_context[f"{task.phase}_output"] = masked
        task.last_updated = datetime.now(timezone.utc)

        cost = event.data.get("cost", 0.0)
        if cost:
            task.total_cost += cost
            task.phase_costs[task.phase] = task.phase_costs.get(task.phase, 0.0) + cost

        # Git checkpoint after phase completion
        commit_hash = await git_checkpoint(
            task.working_directory, task.run_id, task.phase
        )
        if commit_hash:
            task.phase_context[f"{task.phase}_checkpoint"] = commit_hash

        # Update memory file from the orchestrator side so it reflects
        # true system state even if the agent didn't write it.
        elapsed = ""
        if task.phase_started_at:
            delta = datetime.now(timezone.utc) - task.phase_started_at
            elapsed = f"{int(delta.total_seconds())}s"
        result_summary = masked[:80].replace("\n", " ") if masked else "done"
        task_memory.append_progress_row(
            task.run_id,
            task.working_directory,
            action=task.phase,
            result=result_summary,
            elapsed=elapsed,
        )
        task_memory.update_checkpoint(
            task.run_id,
            task.working_directory,
            next_phase=f"after-{task.phase}",
            retries=task.retry_count,
            git_hash=commit_hash,
        )

        await self.store.save(task)

        bg = asyncio.create_task(self._advance(task))
        self._running_tasks[chat_id] = bg

        def _on_advance_done(t: asyncio.Task[None]) -> None:
            self._running_tasks.pop(chat_id, None)
            if not t.cancelled() and t.exception():
                logger.error(
                    "task_advance_failed",
                    run_id=task.run_id,
                    error=str(t.exception()),
                )

        bg.add_done_callback(_on_advance_done)

    async def _on_user_message(self, event: Event) -> None:
        chat_id = event.data.get("chat_id", "")
        text = event.data.get("text", "").strip().lower()

        task = self._active_tasks.get(chat_id)
        if task is None or task.is_terminal():
            return

        if text in ("/cancel", "/stop", "/clear"):
            await self._cancel_task(task, "User cancelled")

    async def _on_config_reloaded(self, event: Event) -> None:
        new_backend = event.data.get("browser_backend")
        if new_backend and new_backend != self._browser_backend:
            logger.info(
                "agentic_orchestrator_backend_updated",
                old=self._browser_backend,
                new=new_backend,
            )
            self._browser_backend = new_backend

    # ── Conductor loop ─────────────────────────────────────────────────

    async def _advance(self, task: TaskRun, *, is_first_call: bool = False) -> None:
        await self._queue.enqueue(
            task.chat_id,
            lambda: self._do_advance(task, is_first_call=is_first_call),
        )

    async def _do_advance(self, task: TaskRun, *, is_first_call: bool = False) -> None:
        if task.is_terminal():
            return

        memory = task_memory.read(
            task.run_id,
            task.working_directory,
            max_chars=self._memory_max_chars,
        )

        last_output = task.phase_context.get(f"{task.phase}_output", "")

        # Summarize verbose phase output for the conductor
        if last_output and len(last_output) > 500 and not is_first_call:
            last_output = await summarize_phase_output(
                task.phase,
                last_output,
                task.task,
                model=self._conductor_model,
                timeout=20.0,
            )
            # Cache the summary so we don't re-summarize on restart
            task.phase_context[f"{task.phase}_summary"] = last_output

        decision = await decide_next_action(
            task_description=task.task,
            memory_content=memory,
            last_output=last_output,
            current_phase=task.phase,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            is_first_call=is_first_call,
            model=self._conductor_model,
            timeout=self._conductor_timeout,
        )

        logger.info(
            "conductor_decided",
            run_id=task.run_id,
            action=decision.action,
            reason=decision.reason,
            complexity=decision.complexity,
        )

        # Track consecutive conductor failures to avoid infinite loops
        if "unparseable" in decision.reason:
            failures = task.phase_context.get("_conductor_parse_failures", 0) + 1
            task.phase_context["_conductor_parse_failures"] = failures
            if failures >= 3:
                logger.warning(
                    "conductor_parse_failures_exhausted",
                    run_id=task.run_id,
                    count=failures,
                )
                task.transition_to("escalated")
                task.error_message = (
                    f"Conductor produced {failures} consecutive unparseable responses"
                )
                await self.store.save(task)
                await self._handle_terminal(task)
                return
        elif "conductor call failed" in decision.reason:
            cli_failures = task.phase_context.get("_conductor_cli_failures", 0) + 1
            task.phase_context["_conductor_cli_failures"] = cli_failures
            if cli_failures >= 3:
                logger.warning(
                    "conductor_cli_failures_exhausted",
                    run_id=task.run_id,
                    count=cli_failures,
                )
                task.transition_to("escalated")
                task.error_message = (
                    f"Conductor CLI failed {cli_failures} consecutive times "
                    "— possible API rate limiting or service outage"
                )
                await self.store.save(task)
                await self._handle_terminal(task)
                return
        else:
            task.phase_context.pop("_conductor_parse_failures", None)
            task.phase_context.pop("_conductor_cli_failures", None)

        # Handle terminal decisions
        if decision.action == "complete":
            task.transition_to("completed")
            await self.store.save(task)
            await self._handle_terminal(task)
            return

        if decision.action == "escalate":
            task.transition_to("escalated")
            task.error_message = decision.reason
            await self.store.save(task)
            await self._handle_terminal(task)
            return

        # Handle PR skip if auto_pr is disabled
        if decision.action == "pr" and not self._auto_pr:
            task.transition_to("completed")
            await self.store.save(task)
            await self._handle_terminal(task)
            return

        # Track fix retries
        if decision.action == "fix":
            task.retry_count += 1
            if task.retry_count > task.max_retries:
                task.transition_to("escalated")
                task.error_message = f"Fix retries exhausted ({task.retry_count})"
                await self.store.save(task)
                await self._handle_terminal(task)
                return

        # Store complexity from first call
        if decision.complexity:
            task.complexity = decision.complexity

        # Transition to new action
        task.transition_to(decision.action)
        await self.store.save(task)

        if self._event_bus:
            await self._event_bus.emit(
                Event(
                    name=TASK_PHASE_CHANGED,
                    data={
                        "run_id": task.run_id,
                        "chat_id": task.chat_id,
                        "phase": task.phase,
                        "previous_phase": task.previous_phase,
                    },
                )
            )

        if self._connector:
            complexity_str = f" [{task.complexity}]" if task.complexity else ""
            display_reason = decision.reason
            if "conductor call failed" in decision.reason:
                display_reason = (
                    "AI orchestrator temporarily unavailable "
                    "— proceeding with best-effort action"
                )
            await self._connector.send_message(
                task.chat_id,
                f"Task action: *{decision.action}*{complexity_str}\n_{display_reason}_",
            )
            await self._connector.send_task_update(
                task.chat_id,
                decision.action,
                "running",
                display_reason,
                complexity=task.complexity,
                reason=decision.reason,
                retry_count=task.retry_count,
                previous_phase=task.previous_phase,
            )

        await self._execute_action(task, decision, memory)

    # ── Action execution ───────────────────────────────────────────────

    async def _execute_action(
        self,
        task: TaskRun,
        decision: ConductorDecision,
        memory: str | None,
    ) -> None:
        if not self._engine:
            logger.error("agentic_orchestrator_no_engine", run_id=task.run_id)
            task.error_message = "Engine not available"
            task.transition_to("failed")
            await self._handle_terminal(task)
            return

        self._engine.disable_auto_approve(task.chat_id)

        mode = _ACTION_TO_MODE.get(decision.action, "auto")

        session = await self._engine.session_manager.get_or_create(
            task.user_id, task.chat_id, task.working_directory
        )
        session.mode = mode
        session.task_run_id = task.run_id
        if mode == "plan":
            session.plan_origin = "task"

        # Build prompt — test/verify phases get special setup
        if decision.action in ("test", "verify"):
            prompt = self._setup_test_or_verify(task, decision, session, memory)
        else:
            prompt = _build_action_prompt(task, decision, memory, self._browser_backend)

        # Set up auto-approvals based on action type
        self._setup_auto_approvals(task.chat_id, decision.action)

        try:
            await self._engine.handle_message(task.user_id, prompt, task.chat_id)
        except asyncio.CancelledError:
            logger.info(
                "task_action_cancelled",
                run_id=task.run_id,
                action=decision.action,
            )
            raise
        except Exception:
            logger.exception(
                "task_action_error",
                run_id=task.run_id,
                action=decision.action,
            )
            task.error_message = f"Action {decision.action} failed with runtime error"
            task.transition_to("failed")
            task.outcome = "error"
            await self.store.save(task)
            await self._handle_terminal(task)

    def _setup_auto_approvals(self, chat_id: str, action: str) -> None:
        """Configure tool auto-approvals based on the current action."""
        if not self._engine:
            return

        if action in ("explore", "review"):
            # Read-only bash + Write/Edit for task memory file updates
            self._engine.enable_tool_auto_approve(chat_id, "Write")
            self._engine.enable_tool_auto_approve(chat_id, "Edit")
            for key in _READ_ONLY_BASH:
                self._engine.enable_tool_auto_approve(chat_id, key)

        elif action in ("implement", "fix"):
            self._engine.enable_tool_auto_approve(chat_id, "Write")
            self._engine.enable_tool_auto_approve(chat_id, "Edit")
            self._engine.enable_tool_auto_approve(chat_id, "NotebookEdit")
            for key in IMPLEMENT_BASH_AUTO_APPROVE:
                self._engine.enable_tool_auto_approve(chat_id, key)

        elif action in ("test", "verify"):
            self._engine.enable_tool_auto_approve(chat_id, "Write")
            self._engine.enable_tool_auto_approve(chat_id, "Edit")
            for tool in BROWSER_READONLY_TOOLS | BROWSER_MUTATION_TOOLS:
                self._engine.enable_tool_auto_approve(chat_id, tool)
            for key in AGENT_BROWSER_AUTO_APPROVE:
                self._engine.enable_tool_auto_approve(chat_id, key)
            for key in TEST_BASH_AUTO_APPROVE:
                self._engine.enable_tool_auto_approve(chat_id, key)

        elif action == "pr":
            self._engine.enable_tool_auto_approve(chat_id, "Write")
            self._engine.enable_tool_auto_approve(chat_id, "Edit")
            for key in IMPLEMENT_BASH_AUTO_APPROVE:
                self._engine.enable_tool_auto_approve(chat_id, key)
            self._engine.enable_tool_auto_approve(chat_id, "Bash::git")
            self._engine.enable_tool_auto_approve(chat_id, "Bash::gh")

    def _setup_test_or_verify(
        self,
        task: TaskRun,
        decision: ConductorDecision,
        session: Any,
        memory: str | None,
    ) -> str:
        """Build a rich prompt for test or verify actions."""
        engine = self._engine
        if engine is None:
            raise RuntimeError("Engine not set")

        config = TestConfig(include_e2e=True, include_unit=True, include_backend=True)
        project_config = load_project_test_config(task.working_directory)
        if project_config:
            config = merge_project_config(config, project_config)

        explicit_specs = project_config.api_specs if project_config else None
        api_specs = discover_api_specs(
            task.working_directory,
            explicit_paths=explicit_specs or None,
        )

        session.mode = "test"
        session.mode_instruction = build_test_instruction(
            config,
            project_config=project_config,
            api_specs=api_specs or None,
            browser_backend=self._browser_backend,
        )

        # Build prompt with memory context
        lines = [
            f"AUTONOMOUS TASK — Action: {decision.action}",
            f"TASK: {task.task}",
            f"WORKING DIRECTORY: {task.working_directory}",
            "",
            f"INSTRUCTION: {decision.instruction}",
            "",
        ]

        if memory:
            lines.append(
                f"TASK MEMORY (.leashd/tasks/{task.run_id}.md):\n```\n{memory}\n```"
            )
            lines.append("")

        session_context = read_test_session_context(task.working_directory)
        if session_context:
            lines.append(
                "PREVIOUS TEST SESSION CONTEXT (from .leashd/test-session.md):"
            )
            lines.append(f"```\n{session_context}\n```")
            lines.append("Resume from this state. Do NOT restart completed phases.")

        if decision.action == "verify":
            suffix = _verify_suffix(self._browser_backend)
        else:
            suffix = _ACTION_SUFFIXES.get(decision.action, "")
        if suffix:
            lines.append("")
            lines.append(suffix)

        return "\n".join(lines)

    # ── Restart recovery ───────────────────────────────────────────────

    async def _resume_task(self, task: TaskRun) -> None:
        if self._connector:
            await self._connector.send_message(
                task.chat_id,
                f"Daemon restarted. Resuming task from: *{task.phase}*\n"
                f"Task: {task.task[:100]}",
            )

        if self._event_bus:
            await self._event_bus.emit(
                Event(
                    name=TASK_RESUMED,
                    data={
                        "run_id": task.run_id,
                        "chat_id": task.chat_id,
                        "phase": task.phase,
                    },
                )
            )

        # Try fast-path: if the memory file has a valid checkpoint with a
        # known next phase, use it directly instead of calling the conductor.
        checkpoint = task_memory.get_checkpoint(task.run_id, task.working_directory)
        checkpoint_next = checkpoint.get("next", "")

        memory = task_memory.read(
            task.run_id,
            task.working_directory,
            max_chars=self._memory_max_chars,
        )

        if checkpoint_next and checkpoint_next in _ACTION_TO_MODE:
            logger.info(
                "task_resume_fast_path",
                run_id=task.run_id,
                next_phase=checkpoint_next,
            )
            decision = ConductorDecision(
                action=checkpoint_next,  # type: ignore[arg-type]
                reason="resumed from memory file checkpoint",
                instruction="Continue the task from where you left off.",
            )

            async def _fast_resume() -> None:
                await self._execute_action(task, decision, memory)

            bg = asyncio.create_task(self._queue.enqueue(task.chat_id, _fast_resume))
        elif memory:
            decision = await decide_next_action(
                task_description=task.task,
                memory_content=memory,
                last_output="(resumed after daemon restart)",
                current_phase=task.phase,
                retry_count=task.retry_count,
                max_retries=task.max_retries,
                model=self._conductor_model,
                timeout=self._conductor_timeout,
            )

            async def _conductor_resume() -> None:
                await self._execute_action(task, decision, memory)

            bg = asyncio.create_task(
                self._queue.enqueue(task.chat_id, _conductor_resume)
            )
        else:
            # No memory file — fall back to a fresh conductor call
            bg = asyncio.create_task(self._advance(task))

        self._running_tasks[task.chat_id] = bg

        def _on_resume_done(t: asyncio.Task[None]) -> None:
            self._running_tasks.pop(task.chat_id, None)
            if not t.cancelled() and t.exception():
                logger.error(
                    "task_resume_failed",
                    run_id=task.run_id,
                    error=str(t.exception()),
                )

        bg.add_done_callback(_on_resume_done)

    # ── Terminal state handling ─────────────────────────────────────────

    async def _handle_terminal(self, task: TaskRun) -> None:
        self._active_tasks.pop(task.chat_id, None)

        if self._engine:
            session = self._engine.session_manager.get(task.user_id, task.chat_id)
            if session:
                session.mode = "default"
                session.mode_instruction = None
                session.task_run_id = None
                session.plan_origin = None
                await self._engine.session_manager.save(session)
            self._engine.disable_auto_approve(task.chat_id)

        if task.phase == "completed":
            msg = self._completed_message(task)
            await self._finalize(task, "ok", TASK_COMPLETED, msg)

        elif task.phase == "escalated":
            msg = self._escalated_message(task)
            await self._finalize(task, "escalated", TASK_ESCALATED, msg)

        elif task.phase == "failed":
            error = task.error_message or "Unknown error"
            await self._finalize(task, "error", TASK_FAILED, f"Task failed: {error}")

        elif task.phase == "cancelled":
            await self._finalize(task, "cancelled", TASK_CANCELLED, None)

        logger.info(
            "task_terminal",
            run_id=task.run_id,
            chat_id=task.chat_id,
            phase=task.phase,
            outcome=task.outcome,
            total_cost=task.total_cost,
            retry_count=task.retry_count,
            complexity=task.complexity,
        )

    async def _finalize(
        self,
        task: TaskRun,
        outcome: TaskOutcome,
        event_name: str,
        message: str | None,
    ) -> None:
        task.outcome = outcome
        await self.store.save(task)
        if message and self._connector:
            await self._connector.send_message(task.chat_id, message)
        if self._event_bus:
            await self._event_bus.emit(
                Event(
                    name=event_name,
                    data={
                        "run_id": task.run_id,
                        "chat_id": task.chat_id,
                        "total_cost": task.total_cost,
                        "complexity": task.complexity,
                        "retry_count": task.retry_count,
                        "error": task.error_message,
                    },
                )
            )

    @staticmethod
    def _completed_message(task: TaskRun) -> str:
        msg = "Task completed successfully."
        if task.total_cost:
            msg += f" Total cost: ${task.total_cost:.4f}"
        if task.complexity:
            msg += f" Complexity: {task.complexity}"
        return msg

    @staticmethod
    def _escalated_message(task: TaskRun) -> str:
        last_output = ""
        for key in ("fix_output", "test_output", "verify_output"):
            out = task.phase_context.get(key, "")
            if out:
                last_output = out[-500:]
                break
        reason = task.error_message or "Unknown reason"
        msg = (
            f"*Task needs human intervention*\n\n"
            f"*Reason:* {reason}\n"
            f"*Retries:* {task.retry_count}\n\n"
        )
        if last_output:
            msg += f"*Last output:*\n```\n{last_output}\n```\n\n"
        msg += "Reply to take over manually."
        return msg

    async def _cancel_task(self, task: TaskRun, reason: str) -> None:
        bg = self._running_tasks.pop(task.chat_id, None)
        if bg and not bg.done():
            bg.cancel()

        if self._engine:
            session_id = self._engine.get_executing_session_id(task.chat_id)
            if session_id:
                await self._engine.agent.cancel(session_id)

        task.error_message = reason
        task.transition_to("cancelled")
        await self.store.save(task)
        await self._handle_terminal(task)

        if self._connector:
            await self._connector.send_message(
                task.chat_id,
                f"Task cancelled: {reason}",
            )

        logger.info(
            "task_cancelled",
            run_id=task.run_id,
            chat_id=task.chat_id,
            reason=reason,
        )

    # ── Stale cleanup ──────────────────────────────────────────────────

    async def cleanup_stale(self, max_age_hours: int = _STALE_TASK_HOURS) -> int:
        active = await self.store.load_all_active()
        now = datetime.now(timezone.utc)
        cleaned = 0
        for task in active:
            age_hours = (now - task.last_updated).total_seconds() / 3600
            if age_hours > max_age_hours:
                task.error_message = f"Stale task (no update for {age_hours:.1f}h)"
                task.transition_to("failed")
                task.outcome = "timeout"
                await self.store.save(task)
                self._active_tasks.pop(task.chat_id, None)
                cleaned += 1
                logger.warning(
                    "task_stale_cleanup",
                    run_id=task.run_id,
                    age_hours=age_hours,
                )
        return cleaned

    # ── Public API ─────────────────────────────────────────────────────

    @property
    def active_tasks(self) -> dict[str, TaskRun]:
        return dict(self._active_tasks)

    def get_task(self, chat_id: str) -> TaskRun | None:
        return self._active_tasks.get(chat_id)
