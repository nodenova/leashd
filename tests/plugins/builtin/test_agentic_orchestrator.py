"""Tests for the AgenticOrchestrator (v2 task orchestrator)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leashd.core import task_memory
from leashd.core.events import Event, EventBus
from leashd.core.task import TaskRun
from leashd.plugins.builtin._conductor import ConductorDecision
from leashd.plugins.builtin.agentic_orchestrator import (
    AgenticOrchestrator,
    _build_action_prompt,
    _verify_suffix,
)


def _make_task(working_dir: str = "/tmp/test", **kwargs) -> TaskRun:
    defaults = {
        "user_id": "u1",
        "chat_id": "c1",
        "session_id": "s1",
        "task": "Add a hello endpoint",
        "working_directory": working_dir,
    }
    defaults.update(kwargs)
    return TaskRun(**defaults)


class _MockSession:
    def __init__(self):
        self.mode = "default"
        self.mode_instruction = None
        self.task_run_id = None
        self.plan_origin = None
        self.chat_id = "c1"


class _MockSessionManager:
    def __init__(self):
        self._session = _MockSession()

    async def get_or_create(self, user_id, chat_id, working_dir):
        return self._session

    def get(self, user_id, chat_id):
        return self._session

    async def save(self, session):
        pass


class _MockEngine:
    def __init__(self):
        self.session_manager = _MockSessionManager()
        self.agent = MagicMock()
        self.agent.cancel = AsyncMock()
        self._handle_message_mock = AsyncMock(return_value="ok")
        self._auto_approvals: dict[str, set[str]] = {}

    async def handle_message(self, user_id, text, chat_id, attachments=None):
        return await self._handle_message_mock(
            user_id, text, chat_id, attachments=attachments
        )

    def enable_tool_auto_approve(self, chat_id, tool_name):
        self._auto_approvals.setdefault(chat_id, set()).add(tool_name)

    def disable_auto_approve(self, chat_id):
        self._auto_approvals.pop(chat_id, None)

    def get_executing_session_id(self, chat_id):
        return None


class TestBuildActionPrompt:
    def test_includes_task_and_instruction(self):
        task = _make_task()
        decision = ConductorDecision(
            action="implement",
            reason="ready to code",
            instruction="Add GET /hello endpoint returning JSON",
        )
        prompt = _build_action_prompt(task, decision, None)
        assert "AUTONOMOUS TASK" in prompt
        assert "implement" in prompt
        assert "Add a hello endpoint" in prompt
        assert "Add GET /hello endpoint returning JSON" in prompt

    def test_includes_memory_when_present(self):
        task = _make_task()
        decision = ConductorDecision(
            action="implement",
            reason="ready",
            instruction="go",
        )
        prompt = _build_action_prompt(
            task, decision, "## Codebase Context\nFlask app at src/app.py"
        )
        assert "Flask app at src/app.py" in prompt
        assert "TASK MEMORY" in prompt

    def test_asks_to_create_memory_when_absent(self):
        task = _make_task()
        decision = ConductorDecision(
            action="explore", reason="first step", instruction="look around"
        )
        prompt = _build_action_prompt(task, decision, None)
        assert "Create and maintain" in prompt

    def test_pr_includes_base_branch(self):
        task = _make_task()
        task.phase_context["auto_pr_base_branch"] = "develop"
        decision = ConductorDecision(
            action="pr", reason="ready", instruction="create PR"
        )
        prompt = _build_action_prompt(task, decision, None)
        assert "develop" in prompt


class TestAgenticOrchestratorLifecycle:
    @pytest.fixture
    def orchestrator(self, tmp_path):
        return AgenticOrchestrator(
            db_path=str(tmp_path / "test.db"),
            max_retries=3,
            auto_pr=False,
        )

    async def test_start_creates_store(self, orchestrator):
        await orchestrator.start()
        assert orchestrator._store is not None
        await orchestrator.stop()

    async def test_stop_clears_state(self, orchestrator):
        await orchestrator.start()
        await orchestrator.stop()
        assert orchestrator._active_tasks == {}
        assert orchestrator._running_tasks == {}


class TestAgenticOrchestratorTaskSubmission:
    @pytest.fixture
    def orchestrator(self, tmp_path):
        orch = AgenticOrchestrator(
            db_path=str(tmp_path / "test.db"),
            max_retries=3,
            auto_pr=False,
        )
        orch._engine = _MockEngine()
        return orch

    async def test_on_task_submitted_creates_task_and_memory(
        self, orchestrator, tmp_path
    ):
        await orchestrator.start()
        event_bus = EventBus()
        ctx = MagicMock()
        ctx.event_bus = event_bus
        await orchestrator.initialize(ctx)

        working_dir = str(tmp_path / "project")
        (tmp_path / "project").mkdir()

        with patch(
            "leashd.plugins.builtin.agentic_orchestrator.decide_next_action",
            new_callable=AsyncMock,
            return_value=ConductorDecision(
                action="explore",
                reason="need context",
                instruction="look around",
                complexity="moderate",
            ),
        ):
            event = Event(
                name="task.submitted",
                data={
                    "user_id": "u1",
                    "chat_id": "c1",
                    "session_id": "s1",
                    "task": "Add endpoint",
                    "working_directory": working_dir,
                },
            )
            await orchestrator._on_task_submitted(event)

            # Task should be active
            assert "c1" in orchestrator._active_tasks
            task = orchestrator._active_tasks["c1"]

            # Memory file should exist
            assert task_memory.exists(task.run_id, working_dir)

            # Wait for background advance to settle
            await asyncio.sleep(0.1)

        await orchestrator.stop()

    async def test_rejects_duplicate_task(self, orchestrator, tmp_path):
        await orchestrator.start()

        connector = AsyncMock()
        orchestrator._connector = connector

        # Manually add an active task
        task = _make_task(working_dir=str(tmp_path))
        orchestrator._active_tasks["c1"] = task

        event = Event(
            name="task.submitted",
            data={
                "user_id": "u1",
                "chat_id": "c1",
                "session_id": "s1",
                "task": "Another task",
                "working_directory": str(tmp_path),
            },
        )
        await orchestrator._on_task_submitted(event)

        connector.send_message.assert_called_once()
        assert "already running" in connector.send_message.call_args[0][1]

        await orchestrator.stop()


class TestAgenticOrchestratorAutoApprovals:
    @pytest.fixture
    def setup(self, tmp_path):
        orch = AgenticOrchestrator(
            db_path=str(tmp_path / "test.db"),
            max_retries=3,
        )
        engine = _MockEngine()
        orch._engine = engine
        return orch, engine

    def test_explore_gets_read_only_and_write(self, setup):
        orch, engine = setup
        orch._setup_auto_approvals("c1", "explore")
        approved = engine._auto_approvals.get("c1", set())
        assert "Bash::cat" in approved
        assert "Bash::ls" in approved
        assert "Write" in approved
        assert "Edit" in approved

    def test_implement_gets_write_tools(self, setup):
        orch, engine = setup
        orch._setup_auto_approvals("c1", "implement")
        approved = engine._auto_approvals.get("c1", set())
        assert "Write" in approved
        assert "Edit" in approved
        assert "Bash::uv run pytest" in approved

    def test_test_gets_browser_tools(self, setup):
        orch, engine = setup
        orch._setup_auto_approvals("c1", "test")
        approved = engine._auto_approvals.get("c1", set())
        # Playwright MCP tools
        assert "browser_navigate" in approved
        assert "browser_click" in approved
        # agent-browser CLI tools
        assert "Bash::agent-browser click" in approved
        assert "Bash::agent-browser open" in approved
        assert "Write" in approved

    def test_review_gets_read_only_and_write(self, setup):
        orch, engine = setup
        orch._setup_auto_approvals("c1", "review")
        approved = engine._auto_approvals.get("c1", set())
        assert "Bash::cat" in approved
        assert "Write" in approved
        assert "Edit" in approved

    def test_pr_gets_git_tools(self, setup):
        orch, engine = setup
        orch._setup_auto_approvals("c1", "pr")
        approved = engine._auto_approvals.get("c1", set())
        assert "Bash::git" in approved
        assert "Bash::gh" in approved
        assert "Write" in approved

    def test_verify_gets_both_browser_backends(self, setup):
        orch, engine = setup
        orch._setup_auto_approvals("c1", "verify")
        approved = engine._auto_approvals.get("c1", set())
        # Playwright MCP tools
        assert "browser_navigate" in approved
        assert "browser_click" in approved
        assert "browser_snapshot" in approved
        # agent-browser CLI tools
        assert "Bash::agent-browser click" in approved
        assert "Bash::agent-browser open" in approved
        assert "Bash::agent-browser snapshot" in approved
        assert "Write" in approved

    def test_test_approves_both_backends_regardless_of_setting(self, setup):
        orch, engine = setup
        orch._browser_backend = "agent-browser"
        orch._setup_auto_approvals("c1", "test")
        approved = engine._auto_approvals.get("c1", set())
        # Both backends approved regardless of active backend
        assert "browser_navigate" in approved
        assert "Bash::agent-browser click" in approved


class TestAgenticOrchestratorTerminal:
    @pytest.fixture
    async def orchestrator(self, tmp_path):
        orch = AgenticOrchestrator(
            db_path=str(tmp_path / "test.db"),
            max_retries=3,
        )
        await orch.start()
        orch._engine = _MockEngine()
        return orch

    async def test_completed_sets_outcome(self, orchestrator):
        task = _make_task()
        task.transition_to("completed")
        orchestrator._active_tasks["c1"] = task

        await orchestrator._handle_terminal(task)
        assert task.outcome == "ok"
        assert "c1" not in orchestrator._active_tasks
        await orchestrator.stop()

    async def test_escalated_sets_outcome(self, orchestrator):
        task = _make_task()
        task.error_message = "stuck"
        task.transition_to("escalated")
        orchestrator._active_tasks["c1"] = task

        connector = AsyncMock()
        orchestrator._connector = connector

        await orchestrator._handle_terminal(task)
        assert task.outcome == "escalated"
        connector.send_message.assert_called_once()
        await orchestrator.stop()

    async def test_failed_sets_outcome(self, orchestrator):
        task = _make_task()
        task.error_message = "runtime error"
        task.transition_to("failed")
        orchestrator._active_tasks["c1"] = task

        await orchestrator._handle_terminal(task)
        assert task.outcome == "error"
        await orchestrator.stop()

    async def test_cancelled_sets_outcome(self, orchestrator):
        task = _make_task()
        task.transition_to("cancelled")
        orchestrator._active_tasks["c1"] = task

        await orchestrator._handle_terminal(task)
        assert task.outcome == "cancelled"
        await orchestrator.stop()


class TestAgenticOrchestratorCancel:
    async def test_cancel_transitions_to_cancelled(self, tmp_path):
        orch = AgenticOrchestrator(
            db_path=str(tmp_path / "test.db"),
            max_retries=3,
        )
        await orch.start()
        orch._engine = _MockEngine()

        task = _make_task()
        orch._active_tasks["c1"] = task

        await orch._cancel_task(task, "User cancelled")
        assert task.phase == "cancelled"
        assert task.outcome == "cancelled"
        assert "c1" not in orch._active_tasks
        await orch.stop()


class TestVerifySuffix:
    def test_playwright_mentions_mcp_tools(self):
        suffix = _verify_suffix("playwright")
        assert "browser_navigate" in suffix
        assert "browser_snapshot" in suffix
        assert "browser_console_messages" in suffix
        assert "browser_take_screenshot" in suffix
        assert "agent-browser" not in suffix

    def test_agent_browser_mentions_cli_tools(self):
        suffix = _verify_suffix("agent-browser")
        assert "agent-browser open" in suffix
        assert "agent-browser snapshot" in suffix
        assert "agent-browser console" in suffix
        assert "agent-browser screenshot" in suffix
        assert "browser_navigate" not in suffix


class TestBuildActionPromptBrowserBackend:
    def test_verify_uses_playwright_by_default(self):
        task = _make_task()
        decision = ConductorDecision(
            action="verify", reason="check UI", instruction="verify the form"
        )
        prompt = _build_action_prompt(task, decision, None)
        assert "browser_navigate" in prompt
        assert "agent-browser" not in prompt

    def test_verify_uses_agent_browser_when_specified(self):
        task = _make_task()
        decision = ConductorDecision(
            action="verify", reason="check UI", instruction="verify the form"
        )
        prompt = _build_action_prompt(task, decision, None, "agent-browser")
        assert "agent-browser open" in prompt
        assert "browser_navigate" not in prompt

    def test_non_verify_action_ignores_backend(self):
        task = _make_task()
        decision = ConductorDecision(
            action="implement", reason="code", instruction="write it"
        )
        prompt_pw = _build_action_prompt(task, decision, None, "playwright")
        prompt_ab = _build_action_prompt(task, decision, None, "agent-browser")
        assert prompt_pw == prompt_ab


class TestActionSuffixContent:
    def test_implement_references_project_commands(self):
        task = _make_task()
        decision = ConductorDecision(
            action="implement", reason="ready", instruction="go"
        )
        prompt = _build_action_prompt(task, decision, None)
        assert "CLAUDE.md" in prompt
        assert "Makefile" in prompt
        assert "package.json" in prompt
        assert "NOT generic commands" in prompt

    def test_explore_mentions_claude_md(self):
        task = _make_task()
        decision = ConductorDecision(
            action="explore", reason="first", instruction="look"
        )
        prompt = _build_action_prompt(task, decision, None)
        assert "CLAUDE.md" in prompt

    def test_all_suffixes_have_before_you_finish(self):
        task = _make_task()
        for action in ("explore", "plan", "implement", "test", "fix", "review", "pr"):
            decision = ConductorDecision(action=action, reason="r", instruction="i")
            prompt = _build_action_prompt(task, decision, None)
            assert "BEFORE YOU FINISH" in prompt, (
                f"Action '{action}' missing BEFORE YOU FINISH block"
            )

    def test_memory_instruction_is_mandatory(self):
        task = _make_task()
        decision = ConductorDecision(action="implement", reason="r", instruction="i")
        prompt = _build_action_prompt(task, decision, "some memory")
        assert "MANDATORY" in prompt


class TestAgenticOrchestratorConfigReload:
    async def test_initialize_captures_browser_backend(self, tmp_path):
        orch = AgenticOrchestrator(db_path=str(tmp_path / "test.db"))
        assert orch._browser_backend == "playwright"  # default

        event_bus = EventBus()
        ctx = MagicMock()
        ctx.event_bus = event_bus
        ctx.config = MagicMock()
        ctx.config.browser_backend = "agent-browser"
        await orch.initialize(ctx)
        assert orch._browser_backend == "agent-browser"

    async def test_config_reloaded_updates_backend(self, tmp_path):
        orch = AgenticOrchestrator(db_path=str(tmp_path / "test.db"))
        event_bus = EventBus()
        ctx = MagicMock()
        ctx.event_bus = event_bus
        ctx.config = MagicMock()
        ctx.config.browser_backend = "playwright"
        await orch.initialize(ctx)
        assert orch._browser_backend == "playwright"

        await event_bus.emit(
            Event(
                name="config.reloaded",
                data={"browser_backend": "agent-browser"},
            )
        )
        assert orch._browser_backend == "agent-browser"

    async def test_config_reloaded_ignores_same_backend(self, tmp_path):
        orch = AgenticOrchestrator(db_path=str(tmp_path / "test.db"))
        event_bus = EventBus()
        ctx = MagicMock()
        ctx.event_bus = event_bus
        ctx.config = MagicMock()
        ctx.config.browser_backend = "playwright"
        await orch.initialize(ctx)

        await event_bus.emit(
            Event(
                name="config.reloaded",
                data={"browser_backend": "playwright"},
            )
        )
        assert orch._browser_backend == "playwright"


class TestConductorCliFailureCircuitBreaker:
    @pytest.fixture
    async def orchestrator(self, tmp_path):
        orch = AgenticOrchestrator(
            db_path=str(tmp_path / "test.db"),
            max_retries=3,
            auto_pr=False,
        )
        await orch.start()
        orch._engine = _MockEngine()
        orch._connector = AsyncMock()
        return orch

    async def test_escalates_after_three_cli_failures(self, orchestrator):
        task = _make_task()
        orchestrator._active_tasks["c1"] = task

        cli_fail_decision = ConductorDecision(
            action="implement",
            reason="conductor call failed: claude CLI error (exit 1): (no output)",
            instruction="Proceed with the task based on available context.",
        )

        call_count = 0

        async def mock_advance(t, is_first):
            nonlocal call_count
            call_count += 1

        with patch(
            "leashd.plugins.builtin.agentic_orchestrator.decide_next_action",
            new_callable=AsyncMock,
            return_value=cli_fail_decision,
        ):
            # Simulate 3 consecutive _do_advance calls with CLI failures
            for _ in range(3):
                await orchestrator._do_advance(task, is_first_call=False)
                if task.phase == "escalated":
                    break

        assert task.phase == "escalated"
        assert "CLI failed 3 consecutive times" in task.error_message

        await orchestrator.stop()

    async def test_cli_failure_counter_resets_on_success(self, orchestrator):
        task = _make_task()
        orchestrator._active_tasks["c1"] = task

        cli_fail_decision = ConductorDecision(
            action="implement",
            reason="conductor call failed: timeout",
            instruction="Proceed.",
        )
        success_decision = ConductorDecision(
            action="implement",
            reason="ready to code",
            instruction="Write the feature.",
        )

        with patch(
            "leashd.plugins.builtin.agentic_orchestrator.decide_next_action",
            new_callable=AsyncMock,
        ) as mock_decide:
            # 2 failures, then success, then 2 more failures
            mock_decide.return_value = cli_fail_decision
            await orchestrator._do_advance(task, is_first_call=False)
            await orchestrator._do_advance(task, is_first_call=False)

            mock_decide.return_value = success_decision
            await orchestrator._do_advance(task, is_first_call=False)

            mock_decide.return_value = cli_fail_decision
            await orchestrator._do_advance(task, is_first_call=False)
            await orchestrator._do_advance(task, is_first_call=False)

        # Should NOT have escalated — counter reset after the success
        assert task.phase != "escalated"
        assert task.phase_context.get("_conductor_cli_failures") == 2

        await orchestrator.stop()


class TestConductorFailureDisplayMessage:
    @pytest.fixture
    async def orchestrator(self, tmp_path):
        orch = AgenticOrchestrator(
            db_path=str(tmp_path / "test.db"),
            max_retries=3,
            auto_pr=False,
        )
        await orch.start()
        orch._engine = _MockEngine()
        orch._connector = AsyncMock()
        return orch

    async def test_conductor_failure_shows_friendly_message(self, orchestrator):
        task = _make_task()
        orchestrator._active_tasks["c1"] = task

        decision = ConductorDecision(
            action="implement",
            reason="conductor call failed: claude CLI error (exit 1): (no output)",
            instruction="Proceed.",
        )

        with patch(
            "leashd.plugins.builtin.agentic_orchestrator.decide_next_action",
            new_callable=AsyncMock,
            return_value=decision,
        ):
            await orchestrator._do_advance(task, is_first_call=False)

        msg = orchestrator._connector.send_message.call_args[0][1]
        assert "AI orchestrator temporarily unavailable" in msg
        assert "conductor call failed" not in msg

        await orchestrator.stop()

    async def test_normal_reason_passes_through(self, orchestrator):
        task = _make_task()
        orchestrator._active_tasks["c1"] = task

        decision = ConductorDecision(
            action="implement",
            reason="ready to write code",
            instruction="Implement the feature.",
        )

        with patch(
            "leashd.plugins.builtin.agentic_orchestrator.decide_next_action",
            new_callable=AsyncMock,
            return_value=decision,
        ):
            await orchestrator._do_advance(task, is_first_call=False)

        msg = orchestrator._connector.send_message.call_args[0][1]
        assert "ready to write code" in msg

        await orchestrator.stop()
