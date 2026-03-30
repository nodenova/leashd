"""Tests for TaskMemory persistent working-memory files."""

from __future__ import annotations

from leashd.core import task_memory


class TestSeed:
    def test_creates_file(self, tmp_path):
        fp = task_memory.seed("abc123", "Add a hello endpoint", str(tmp_path))
        assert fp.is_file()
        assert fp.name == "abc123.md"

    def test_content_contains_task(self, tmp_path):
        task_memory.seed("r1", "Fix the login bug", str(tmp_path))
        content = task_memory.read("r1", str(tmp_path))
        assert content is not None
        assert "Fix the login bug" in content
        assert "r1" in content

    def test_truncates_long_task_title(self, tmp_path):
        long_task = "x" * 200
        task_memory.seed("r2", long_task, str(tmp_path))
        content = task_memory.read("r2", str(tmp_path))
        assert content is not None
        assert "..." in content

    def test_creates_tasks_subdirectory(self, tmp_path):
        task_memory.seed("r3", "Test", str(tmp_path))
        assert (tmp_path / ".leashd" / "tasks").is_dir()


class TestPathAndExists:
    def test_path_returns_expected_location(self, tmp_path):
        p = task_memory.path("abc", str(tmp_path))
        assert p.name == "abc.md"
        assert ".leashd/tasks" in str(p)

    def test_exists_false_before_seed(self, tmp_path):
        assert not task_memory.exists("nope", str(tmp_path))

    def test_exists_true_after_seed(self, tmp_path):
        task_memory.seed("yes", "task", str(tmp_path))
        assert task_memory.exists("yes", str(tmp_path))


class TestRead:
    def test_returns_none_if_missing(self, tmp_path):
        assert task_memory.read("missing", str(tmp_path)) is None

    def test_returns_full_content_if_small(self, tmp_path):
        task_memory.seed("r1", "small task", str(tmp_path))
        content = task_memory.read("r1", str(tmp_path))
        assert content is not None
        assert "## Checkpoint" in content

    def test_truncation_preserves_head_and_tail(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        fp = task_memory.path("r1", str(tmp_path))
        # Append a large progress section so the file exceeds max_chars
        content = fp.read_text(encoding="utf-8")
        content += "\n- action " + "x" * 10000
        content += "\n## Checkpoint\nNext: test | Retries: 1 | Blocked: none\n"
        fp.write_text(content, encoding="utf-8")
        result = task_memory.read("r1", str(tmp_path), max_chars=800)
        assert result is not None
        # Head: task description and template sections preserved
        assert "# Task:" in result
        # Tail: checkpoint section preserved
        assert "Checkpoint" in result or "test" in result
        # Truncation marker present
        assert "[...middle truncated...]" in result

    def test_head_preserves_plan_section(self, tmp_path):
        task_memory.seed("r1", "implement feature", str(tmp_path))
        fp = task_memory.path("r1", str(tmp_path))
        content = fp.read_text(encoding="utf-8")
        content = content.replace(
            "(no plan yet)", "Step 1: create module\nStep 2: add tests"
        )
        # Add lots of progress to push past max_chars
        content += "\n| 1 | explore | done | 10s |\n" * 200
        fp.write_text(content, encoding="utf-8")
        result = task_memory.read("r1", str(tmp_path), max_chars=2000)
        assert result is not None
        assert "## Plan" in result
        assert "create module" in result


class TestGetCheckpoint:
    def test_returns_empty_dict_if_missing(self, tmp_path):
        assert task_memory.get_checkpoint("missing", str(tmp_path)) == {}

    def test_parses_default_checkpoint(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        cp = task_memory.get_checkpoint("r1", str(tmp_path))
        assert cp["next"] == "pending"
        assert cp["retries"] == "0"
        assert cp["blocked"] == "none"

    def test_parses_custom_checkpoint(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        fp = task_memory.path("r1", str(tmp_path))
        content = fp.read_text(encoding="utf-8")
        content = content.replace(
            "Next: pending | Retries: 0 | Blocked: none",
            "Next: test | Retries: 2 | Blocked: waiting for API key",
        )
        fp.write_text(content, encoding="utf-8")
        cp = task_memory.get_checkpoint("r1", str(tmp_path))
        assert cp["next"] == "test"
        assert cp["retries"] == "2"
        assert cp["blocked"] == "waiting for API key"


class TestAppendProgressRow:
    def test_appends_first_row(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        ok = task_memory.append_progress_row(
            "r1", str(tmp_path), action="explore", result="done", elapsed="12s"
        )
        assert ok is True
        content = task_memory.read("r1", str(tmp_path))
        assert content is not None
        assert "| 1 | explore | done | 12s |" in content

    def test_appends_multiple_rows(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        task_memory.append_progress_row(
            "r1", str(tmp_path), action="explore", result="done", elapsed="10s"
        )
        task_memory.append_progress_row(
            "r1", str(tmp_path), action="plan", result="done", elapsed="8s"
        )
        content = task_memory.read("r1", str(tmp_path))
        assert content is not None
        assert "| 1 | explore |" in content
        assert "| 2 | plan |" in content

    def test_truncates_long_result(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        task_memory.append_progress_row(
            "r1", str(tmp_path), action="test", result="x" * 200, elapsed="5s"
        )
        content = task_memory.read("r1", str(tmp_path))
        assert content is not None
        assert "..." in content

    def test_returns_false_for_missing_file(self, tmp_path):
        ok = task_memory.append_progress_row(
            "missing", str(tmp_path), action="test", result="ok", elapsed="1s"
        )
        assert ok is False

    def test_preserves_other_sections(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        task_memory.append_progress_row(
            "r1", str(tmp_path), action="explore", result="done", elapsed="10s"
        )
        content = task_memory.read("r1", str(tmp_path))
        assert content is not None
        assert "## Changes" in content
        assert "## Checkpoint" in content

    def test_does_not_duplicate_with_agent_rows(self, tmp_path):
        """Orchestrator rows coexist with rows the agent wrote."""
        task_memory.seed("r1", "task", str(tmp_path))
        # Simulate agent adding a row manually
        fp = task_memory.path("r1", str(tmp_path))
        content = fp.read_text(encoding="utf-8")
        content = content.replace(
            "|---|--------|--------|------|\n",
            "|---|--------|--------|------|\n| 1 | explore | mapped files | 5s |\n",
        )
        fp.write_text(content, encoding="utf-8")
        # Orchestrator appends — should be row 2
        task_memory.append_progress_row(
            "r1", str(tmp_path), action="explore", result="done", elapsed="10s"
        )
        content = task_memory.read("r1", str(tmp_path))
        assert content is not None
        assert "| 1 | explore | mapped files" in content
        assert "| 2 | explore | done" in content


class TestUpdateCheckpoint:
    def test_updates_checkpoint_section(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        ok = task_memory.update_checkpoint(
            "r1", str(tmp_path), next_phase="test", retries=1
        )
        assert ok is True
        cp = task_memory.get_checkpoint("r1", str(tmp_path))
        assert cp["next"] == "test"
        assert cp["retries"] == "1"

    def test_includes_git_hash(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        task_memory.update_checkpoint(
            "r1", str(tmp_path), next_phase="review", git_hash="abc1234"
        )
        cp = task_memory.get_checkpoint("r1", str(tmp_path))
        assert cp["commit"] == "abc1234"

    def test_updates_timestamp(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        fp = task_memory.path("r1", str(tmp_path))
        before = fp.read_text(encoding="utf-8")
        task_memory.update_checkpoint("r1", str(tmp_path), next_phase="implement")
        after = fp.read_text(encoding="utf-8")
        # Updated timestamp should have changed
        assert before != after

    def test_returns_false_for_missing_file(self, tmp_path):
        ok = task_memory.update_checkpoint("missing", str(tmp_path), next_phase="test")
        assert ok is False

    def test_preserves_blocked_field(self, tmp_path):
        task_memory.seed("r1", "task", str(tmp_path))
        task_memory.update_checkpoint(
            "r1", str(tmp_path), next_phase="fix", blocked="test failures"
        )
        cp = task_memory.get_checkpoint("r1", str(tmp_path))
        assert cp["blocked"] == "test failures"
