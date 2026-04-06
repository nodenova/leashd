"""Tests for the context manager — observation masking, phase summarization, and git checkpointing."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from leashd.core.context_manager import (
    git_checkpoint,
    mask_phase_output,
    mask_tool_output,
    summarize_phase_output,
)


class TestMaskToolOutput:
    def test_short_output_unchanged(self):
        output = "test passed\n3 tests, 0 failures"
        assert mask_tool_output(output) == output

    def test_long_output_gets_masked(self):
        output = "x" * 2000
        masked = mask_tool_output(output)
        assert len(masked) < len(output)
        assert "[...output truncated...]" in masked

    def test_preserves_head_and_tail(self):
        head = "=== TEST START ===\n"
        middle = "verbose log line\n" * 200
        tail = "\n=== RESULT: 5 passed ==="
        output = head + middle + tail
        masked = mask_tool_output(output, max_chars=500)
        assert "TEST START" in masked
        assert "RESULT: 5 passed" in masked

    def test_exact_boundary(self):
        output = "a" * 800
        result = mask_tool_output(output, max_chars=800)
        assert result == output

    def test_empty_string(self):
        assert mask_tool_output("") == ""

    def test_custom_max_chars(self):
        output = "x" * 500
        masked = mask_tool_output(output, max_chars=200)
        assert len(masked) < 500
        assert "[...output truncated...]" in masked

    def test_very_small_max_chars_hard_truncates(self):
        output = "x" * 100
        result = mask_tool_output(output, max_chars=10)
        assert result == "x" * 10
        assert "[...output truncated...]" not in result


class TestMaskPhaseOutput:
    def test_uses_phase_budget(self):
        output = "x" * 3000
        masked = mask_phase_output(output)
        assert len(masked) < 3000
        assert "[...output truncated...]" in masked

    def test_short_phase_output_unchanged(self):
        output = "All tests passed in 0.5s"
        assert mask_phase_output(output) == output


class TestSummarizePhaseOutput:
    async def test_short_output_returned_as_is(self):
        result = await summarize_phase_output("test", "3 tests passed", "Add endpoint")
        assert result == "3 tests passed"

    async def test_long_output_gets_summarized(self):
        long_output = "Running tests...\n" + "PASS test_foo.py\n" * 100

        with patch(
            "leashd.plugins.builtin._cli_evaluator.evaluate_via_cli",
            new_callable=AsyncMock,
            return_value="Ran 100 tests, all passed. No failures.",
        ):
            result = await summarize_phase_output("test", long_output, "Add endpoint")
            assert result == "Ran 100 tests, all passed. No failures."

    async def test_falls_back_to_masking_on_timeout(self):
        long_output = "verbose output\n" * 200

        with patch(
            "leashd.plugins.builtin._cli_evaluator.evaluate_via_cli",
            new_callable=AsyncMock,
            side_effect=TimeoutError("timed out"),
        ):
            result = await summarize_phase_output(
                "implement", long_output, "Add endpoint"
            )
            assert "[...output truncated...]" in result

    async def test_falls_back_to_masking_on_runtime_error(self):
        long_output = "verbose output\n" * 200

        with patch(
            "leashd.plugins.builtin._cli_evaluator.evaluate_via_cli",
            new_callable=AsyncMock,
            side_effect=RuntimeError("CLI crashed"),
        ):
            result = await summarize_phase_output("test", long_output, "Fix bug")
            assert "[...output truncated...]" in result

    async def test_falls_back_on_empty_summary(self):
        long_output = "verbose output\n" * 200

        with patch(
            "leashd.plugins.builtin._cli_evaluator.evaluate_via_cli",
            new_callable=AsyncMock,
            return_value="",
        ):
            result = await summarize_phase_output("test", long_output, "Fix bug")
            assert "[...output truncated...]" in result


def _init_git_repo(path: Path) -> None:
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        capture_output=True,
        check=True,
    )


class TestGitCheckpoint:
    async def test_creates_checkpoint_commit(self, tmp_path):
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("print('hello')")

        result = await git_checkpoint(str(tmp_path), "abc123", "implement")
        assert result is not None
        assert len(result) >= 7  # short hash

        # Verify the commit message
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert "leashd-checkpoint: implement" in proc.stdout
        assert "abc123" in proc.stdout

    async def test_returns_none_when_nothing_to_commit(self, tmp_path):
        _init_git_repo(tmp_path)
        result = await git_checkpoint(str(tmp_path), "abc123", "explore")
        assert result is None

    async def test_returns_none_when_not_git_repo(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        result = await git_checkpoint(str(tmp_path), "abc123", "implement")
        assert result is None

    async def test_commit_message_format(self, tmp_path):
        _init_git_repo(tmp_path)
        (tmp_path / "new_file.py").write_text("x = 1")

        await git_checkpoint(str(tmp_path), "run42", "test")

        proc = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        body = proc.stdout
        assert "Phase completed: test" in body
        assert "Task run: run42" in body

    async def test_custom_message(self, tmp_path):
        _init_git_repo(tmp_path)
        (tmp_path / "fix.py").write_text("fixed = True")

        await git_checkpoint(
            str(tmp_path),
            "abc",
            "fix",
            message="custom checkpoint message",
        )

        proc = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert "custom checkpoint message" in proc.stdout

    async def test_nothing_to_commit_after_staging_returns_none(self, tmp_path):
        """Race condition: changes staged but already committed by another process."""
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("print('hello')")

        call_count = 0
        real_create = asyncio.create_subprocess_exec

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:  # git commit
                proc = MagicMock()
                proc.communicate = AsyncMock(
                    return_value=(b"", b"nothing to commit, working tree clean")
                )
                proc.returncode = 1
                return proc
            return await real_create(*args, **kwargs)

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await git_checkpoint(str(tmp_path), "abc", "test")

        assert result is None

    async def test_commit_failure_returns_none(self, tmp_path):
        """Git commit failure (corrupted index, hook error) must not crash the task."""
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("print('hello')")

        call_count = 0
        real_create = asyncio.create_subprocess_exec

        async def _mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:  # the git commit call
                proc = MagicMock()
                proc.communicate = AsyncMock(
                    return_value=(b"", b"error: could not write commit object")
                )
                proc.returncode = 128
                return proc
            return await real_create(*args, **kwargs)

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await git_checkpoint(str(tmp_path), "abc123", "implement")

        assert result is None

    async def test_timeout_during_large_repo(self, tmp_path):
        """Git hanging on a large repo must not block the task loop."""
        _init_git_repo(tmp_path)
        (tmp_path / "big.txt").write_text("x" * 1000)

        real_create = asyncio.create_subprocess_exec

        async def _mock_exec(*args, **kwargs):
            proc = await real_create(*args, **kwargs)

            async def _slow_communicate(*a, **kw):
                raise TimeoutError("git timed out")

            proc.communicate = _slow_communicate
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec):
            result = await git_checkpoint(str(tmp_path), "abc", "test")

        assert result is None

    async def test_git_not_installed_returns_none(self, tmp_path):
        """Missing git binary (containerized agent) must not crash."""
        _init_git_repo(tmp_path)
        (tmp_path / "file.py").write_text("x = 1")

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("No such file or directory: 'git'"),
        ):
            result = await git_checkpoint(str(tmp_path), "abc", "implement")

        assert result is None


class TestOrchestratorIntegration:
    """Test that the orchestrator correctly integrates context management."""

    async def test_session_completed_uses_masking(self):
        """Verify mask_phase_output is used instead of TaskStore.truncate_context."""
        long_output = "verbose line\n" * 300  # ~4000 chars

        masked = mask_phase_output(long_output)
        assert "[...output truncated...]" in masked
        assert len(masked) < len(long_output)

    async def test_build_prompt_includes_checkpoints(self):
        from leashd.core.task import TaskRun
        from leashd.plugins.builtin._conductor import ConductorDecision
        from leashd.plugins.builtin.agentic_orchestrator import _build_action_prompt

        task = TaskRun(
            user_id="u1",
            chat_id="c1",
            session_id="s1",
            task="Add feature",
            working_directory="/tmp/test",
        )
        task.phase_context["explore_checkpoint"] = "abc1234"
        task.phase_context["implement_checkpoint"] = "def5678"

        decision = ConductorDecision(
            action="test", reason="ready", instruction="run tests"
        )
        prompt = _build_action_prompt(task, decision, None)
        assert "GIT CHECKPOINTS" in prompt
        assert "explore: abc1234" in prompt
        assert "implement: def5678" in prompt

    async def test_build_prompt_no_checkpoints_no_section(self):
        from leashd.core.task import TaskRun
        from leashd.plugins.builtin._conductor import ConductorDecision
        from leashd.plugins.builtin.agentic_orchestrator import _build_action_prompt

        task = TaskRun(
            user_id="u1",
            chat_id="c1",
            session_id="s1",
            task="Add feature",
            working_directory="/tmp/test",
        )
        decision = ConductorDecision(
            action="implement", reason="go", instruction="write code"
        )
        prompt = _build_action_prompt(task, decision, None)
        assert "GIT CHECKPOINTS" not in prompt
