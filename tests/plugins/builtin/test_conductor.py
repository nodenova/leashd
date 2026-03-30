"""Tests for the conductor module — AI-driven orchestration decisions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from leashd.plugins.builtin._conductor import (
    ConductorDecision,
    _parse_response,
    decide_next_action,
)


class TestParseResponse:
    def test_parses_json_response(self):
        raw = '{"action": "explore", "reason": "need context", "instruction": "read src/"}'
        result = _parse_response(raw)
        assert result.action == "explore"
        assert result.reason == "need context"
        assert result.instruction == "read src/"

    def test_parses_json_with_complexity(self):
        raw = '{"action": "implement", "reason": "simple fix", "instruction": "fix it", "complexity": "simple"}'
        result = _parse_response(raw)
        assert result.action == "implement"
        assert result.complexity == "simple"

    def test_parses_json_embedded_in_text(self):
        raw = 'Here is my decision:\n{"action": "test", "reason": "tests needed", "instruction": "run pytest"}\nDone.'
        result = _parse_response(raw)
        assert result.action == "test"

    def test_fallback_to_action_colon_format(self):
        raw = "EXPLORE: need to understand the codebase"
        result = _parse_response(raw)
        assert result.action == "explore"
        assert result.reason == "need to understand the codebase"

    def test_fallback_case_insensitive(self):
        raw = "implement: ready to code"
        result = _parse_response(raw)
        assert result.action == "implement"

    def test_all_valid_actions_parse(self):
        for action in (
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
        ):
            raw = f'{{"action": "{action}", "reason": "test", "instruction": "do it"}}'
            result = _parse_response(raw)
            assert result.action == action

    def test_invalid_action_defaults_to_implement(self):
        raw = '{"action": "dance", "reason": "fun"}'
        result = _parse_response(raw)
        assert result.action == "implement"

    def test_unparseable_defaults_to_implement(self):
        result = _parse_response("just some random text")
        assert result.action == "implement"
        assert "unparseable" in result.reason

    def test_empty_input(self):
        result = _parse_response("")
        assert result.action == "implement"

    def test_invalid_complexity_ignored(self):
        raw = '{"action": "explore", "reason": "x", "instruction": "y", "complexity": "banana"}'
        result = _parse_response(raw)
        assert result.action == "explore"
        assert result.complexity is None

    def test_valid_complexities(self):
        for level in ("trivial", "simple", "moderate", "complex", "critical"):
            raw = f'{{"action": "explore", "reason": "x", "instruction": "y", "complexity": "{level}"}}'
            result = _parse_response(raw)
            assert result.complexity == level


class TestDecideNextAction:
    async def test_returns_decision_on_success(self):
        with patch(
            "leashd.plugins.builtin._conductor.evaluate_via_cli",
            new_callable=AsyncMock,
            return_value='{"action": "explore", "reason": "need context", "instruction": "look around", "complexity": "moderate"}',
        ):
            result = await decide_next_action(
                task_description="Add a feature",
                memory_content=None,
                last_output="",
                current_phase="pending",
                is_first_call=True,
            )
            assert result.action == "explore"
            assert result.complexity == "moderate"

    async def test_falls_back_on_timeout(self):
        with patch(
            "leashd.plugins.builtin._conductor.evaluate_via_cli",
            new_callable=AsyncMock,
            side_effect=TimeoutError("timed out"),
        ):
            result = await decide_next_action(
                task_description="Do something",
                memory_content=None,
                last_output="",
                current_phase="pending",
                is_first_call=True,
            )
            assert result.action == "explore"
            assert "failed" in result.reason

    async def test_falls_back_on_runtime_error(self):
        with patch(
            "leashd.plugins.builtin._conductor.evaluate_via_cli",
            new_callable=AsyncMock,
            side_effect=RuntimeError("CLI crashed"),
        ):
            result = await decide_next_action(
                task_description="Fix a bug",
                memory_content="## Checkpoint\nNext: implement",
                last_output="",
                current_phase="implement",
                is_first_call=False,
            )
            # Not first call, so fallback is implement
            assert result.action == "implement"

    async def test_empty_timeout_error_includes_type_name(self):
        with patch(
            "leashd.plugins.builtin._conductor.evaluate_via_cli",
            new_callable=AsyncMock,
            side_effect=TimeoutError(),
        ):
            result = await decide_next_action(
                task_description="Do something",
                memory_content=None,
                last_output="",
                current_phase="pending",
                is_first_call=True,
            )
            assert "TimeoutError (no details)" in result.reason

    async def test_empty_runtime_error_includes_type_name(self):
        with patch(
            "leashd.plugins.builtin._conductor.evaluate_via_cli",
            new_callable=AsyncMock,
            side_effect=RuntimeError(""),
        ):
            result = await decide_next_action(
                task_description="Do something",
                memory_content=None,
                last_output="",
                current_phase="implement",
                is_first_call=False,
            )
            assert "RuntimeError (no details)" in result.reason

    async def test_includes_memory_in_context(self):
        captured_args = {}

        async def mock_eval(system: str, user: str, **kw):
            captured_args["user"] = user
            return '{"action": "implement", "reason": "ready", "instruction": "go"}'

        with patch(
            "leashd.plugins.builtin._conductor.evaluate_via_cli",
            new_callable=AsyncMock,
            side_effect=mock_eval,
        ):
            await decide_next_action(
                task_description="test",
                memory_content="## Codebase Context\nFound auth module",
                last_output="done exploring",
                current_phase="explore",
            )
            assert "Found auth module" in captured_args["user"]
            assert "done exploring" in captured_args["user"]


class TestConductorDecisionModel:
    def test_frozen(self):
        d = ConductorDecision(action="explore", reason="test")
        with pytest.raises(ValidationError):
            d.action = "plan"  # type: ignore[misc]

    def test_defaults(self):
        d = ConductorDecision(action="implement")
        assert d.reason == ""
        assert d.instruction == ""
        assert d.complexity is None
