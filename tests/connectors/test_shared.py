"""Tests for shared connector utilities."""

import pytest

from leashd.connectors._shared import (
    ParsedResponse,
    activity_label,
    format_text_approval,
    format_text_plan_review,
    format_text_question,
    parse_text_response,
    split_text,
)
from leashd.exceptions import ConnectorError


class TestSplitText:
    def test_empty(self):
        assert split_text("") == [""]

    def test_short_single_chunk(self):
        assert split_text("hello") == ["hello"]

    def test_exact_limit(self):
        text = "a" * 4000
        assert split_text(text, 4000) == [text]

    def test_custom_limit(self):
        text = "a" * 100
        chunks = split_text(text, 50)
        assert len(chunks) == 2
        assert chunks[0] == "a" * 50
        assert chunks[1] == "a" * 50

    def test_splits_at_newline(self):
        line = "a" * 1500
        text = f"{line}\n{line}\n{line}"
        chunks = split_text(text, 4000)
        assert len(chunks) == 2
        assert chunks[0] == f"{line}\n{line}"
        assert chunks[1] == line

    def test_splits_at_space(self):
        word = "a" * 1999
        text = f"{word} {word} {word}"
        chunks = split_text(text, 4000)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 4000

    def test_hard_break(self):
        text = "a" * 5000
        chunks = split_text(text, 4000)
        assert len(chunks) == 2
        assert chunks[0] == "a" * 4000
        assert chunks[1] == "a" * 1000


class TestRetryOnError:
    async def test_success_first_try(self):
        from leashd.connectors._shared import retry_on_error

        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            return "ok"

        result = await retry_on_error(factory, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert calls == 1

    async def test_success_after_retry(self):
        from leashd.connectors._shared import retry_on_error

        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ValueError("transient")
            return "ok"

        result = await retry_on_error(
            factory, max_retries=3, base_delay=0.01, retryable=(ValueError,)
        )
        assert result == "ok"
        assert calls == 3

    async def test_exhausts_retries(self):
        from leashd.connectors._shared import retry_on_error

        async def factory():
            raise ValueError("always fail")

        with pytest.raises(ConnectorError, match="failed after 2 retries"):
            await retry_on_error(
                factory, max_retries=2, base_delay=0.01, retryable=(ValueError,)
            )


class TestActivityLabel:
    def test_bash_search(self):
        _emoji, verb = activity_label("Bash", "ls -la")
        assert verb == "Searching"

    def test_bash_git_read(self):
        _emoji, verb = activity_label("Bash", "git status")
        assert verb == "Searching"

    def test_bash_other(self):
        _emoji, verb = activity_label("Bash", "npm install")
        assert verb == "Running"

    def test_edit_tool(self):
        _emoji, verb = activity_label("Edit")
        assert verb == "Editing"

    def test_search_tool(self):
        _emoji, verb = activity_label("Grep")
        assert verb == "Searching"

    def test_think_tool(self):
        _emoji, verb = activity_label("TodoWrite")
        assert verb == "Thinking"

    def test_browser_tool(self):
        _emoji, verb = activity_label("mcp__playwright__click")
        assert verb == "Browsing"

    def test_unknown_tool(self):
        _emoji, verb = activity_label("SomeUnknown")
        assert verb == "Running"


class TestFormatTextApproval:
    def test_contains_description(self):
        result = format_text_approval("Run npm install")
        assert "Run npm install" in result
        assert "APPROVAL REQUIRED" in result
        assert "approve" in result.lower()

    def test_contains_reply_instructions(self):
        result = format_text_approval("test")
        assert "approve" in result
        assert "reject" in result


class TestFormatTextQuestion:
    def test_numbered_options(self):
        result = format_text_question(
            "Pick one",
            "Choice",
            [{"label": "Alpha"}, {"label": "Beta"}],
        )
        assert "1. Alpha" in result
        assert "2. Beta" in result
        assert "Choice" in result
        assert "Pick one" in result

    def test_empty_header(self):
        result = format_text_question("Q?", "", [{"label": "A"}])
        assert "Q?" in result


class TestFormatTextPlanReview:
    def test_four_options(self):
        result = format_text_plan_review("My plan text")
        assert "My plan text" in result
        assert "1." in result
        assert "2." in result
        assert "3." in result
        assert "4." in result

    def test_truncation(self):
        long_text = "x" * 5000
        result = format_text_plan_review(long_text)
        assert "truncated" in result


class TestParseTextResponse:
    def test_approve(self):
        result = parse_text_response("approve", has_pending_approval=True)
        assert result == ParsedResponse(kind="approval", value="approve")

    def test_yes(self):
        result = parse_text_response("yes", has_pending_approval=True)
        assert result == ParsedResponse(kind="approval", value="approve")

    def test_y(self):
        result = parse_text_response("Y", has_pending_approval=True)
        assert result == ParsedResponse(kind="approval", value="approve")

    def test_reject(self):
        result = parse_text_response("reject", has_pending_approval=True)
        assert result == ParsedResponse(kind="approval", value="reject")

    def test_no(self):
        result = parse_text_response("no", has_pending_approval=True)
        assert result == ParsedResponse(kind="approval", value="reject")

    def test_approve_all(self):
        result = parse_text_response("approve-all", has_pending_approval=True)
        assert result == ParsedResponse(kind="approval", value="approve-all")

    def test_all(self):
        result = parse_text_response("all", has_pending_approval=True)
        assert result == ParsedResponse(kind="approval", value="approve-all")

    def test_no_pending_returns_none(self):
        result = parse_text_response("approve", has_pending_approval=False)
        assert result is None

    def test_numbered_interaction(self):
        options = [{"label": "Alpha"}, {"label": "Beta"}]
        result = parse_text_response(
            "2",
            has_pending_interaction=True,
            pending_options=options,
        )
        assert result == ParsedResponse(kind="interaction", value="Beta")

    def test_out_of_range_number(self):
        options = [{"label": "Alpha"}]
        result = parse_text_response(
            "5",
            has_pending_interaction=True,
            pending_options=options,
        )
        assert result is None

    def test_plan_review_option(self):
        result = parse_text_response("1", has_pending_plan_review=True)
        assert result == ParsedResponse(kind="plan_review", value="clean_edit")

    def test_plan_review_option_4(self):
        result = parse_text_response("4", has_pending_plan_review=True)
        assert result == ParsedResponse(kind="plan_review", value="adjust")

    def test_unrecognized_text(self):
        result = parse_text_response(
            "hello world",
            has_pending_approval=True,
            has_pending_interaction=True,
        )
        assert result is None
