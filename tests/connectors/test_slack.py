"""Tests for the Slack connector."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leashd.connectors.base import InlineButton

# --- Module-level mock to avoid import-time failure ---


@pytest.fixture
def _mock_slack_deps():
    """Patch slack-bolt imports before importing SlackConnector."""
    mock_app_cls = MagicMock()
    mock_handler_cls = MagicMock()
    with (
        patch.dict(
            "sys.modules",
            {
                "slack_bolt": MagicMock(),
                "slack_bolt.async_app": MagicMock(AsyncApp=mock_app_cls),
                "slack_bolt.adapter": MagicMock(),
                "slack_bolt.adapter.socket_mode": MagicMock(),
                "slack_bolt.adapter.socket_mode.async_handler": MagicMock(
                    AsyncSocketModeHandler=mock_handler_cls
                ),
            },
        ),
    ):
        yield mock_app_cls, mock_handler_cls


@pytest.fixture
def connector(_mock_slack_deps):
    from leashd.connectors.slack import SlackConnector

    return SlackConnector(bot_token="xoxb-test", app_token="xapp-test")


def _make_mock_app():
    """Create a mock Slack app with client methods."""
    app = MagicMock()
    app.client = AsyncMock()
    app.client.chat_postMessage = AsyncMock(return_value={"ts": "1234.5678"})
    app.client.chat_update = AsyncMock()
    app.client.chat_delete = AsyncMock()
    app.client.files_upload_v2 = AsyncMock()
    app.client.reactions_add = AsyncMock()
    app.client.reactions_remove = AsyncMock()
    app.event = MagicMock(return_value=lambda f: f)
    app.action = MagicMock(return_value=lambda f: f)
    return app


def _make_action_body(
    action_id, chat_id="C123", message_ts="1234.5678", user_id="U123"
):
    """Helper to build a Slack action body."""
    return {
        "actions": [{"action_id": action_id}],
        "channel": {"id": chat_id},
        "message": {"ts": message_ts},
        "user": {"id": user_id},
    }


class TestSlackSendMessage:
    async def test_sends_short_message(self, connector):
        connector._app = _make_mock_app()
        await connector.send_message("C123", "hello")
        connector._app.client.chat_postMessage.assert_called_once()
        call_kwargs = connector._app.client.chat_postMessage.call_args
        assert call_kwargs[1]["channel"] == "C123"
        assert call_kwargs[1]["text"] == "hello"

    async def test_sends_with_buttons(self, connector):
        connector._app = _make_mock_app()
        buttons = [[InlineButton(text="OK", callback_data="ok")]]
        await connector.send_message("C123", "choose", buttons=buttons)
        call_kwargs = connector._app.client.chat_postMessage.call_args
        assert "blocks" in call_kwargs[1]

    async def test_no_app_no_error(self, connector):
        connector._app = None
        await connector.send_message("C123", "hello")


class TestSlackSendMessageWithId:
    async def test_returns_ts(self, connector):
        connector._app = _make_mock_app()
        ts = await connector.send_message_with_id("C123", "hi")
        assert ts == "1234.5678"

    async def test_no_app_returns_none(self, connector):
        connector._app = None
        ts = await connector.send_message_with_id("C123", "hi")
        assert ts is None


class TestSlackEditMessage:
    async def test_edit_calls_update_with_correct_args(self, connector):
        connector._app = _make_mock_app()
        await connector.edit_message("C123", "1234.5678", "new text")
        connector._app.client.chat_update.assert_called_once()
        call_kwargs = connector._app.client.chat_update.call_args
        assert call_kwargs[1]["channel"] == "C123"
        assert call_kwargs[1]["ts"] == "1234.5678"
        assert call_kwargs[1]["text"] == "new text"

    async def test_edit_no_app_returns_early(self, connector):
        connector._app = None
        await connector.edit_message("C123", "1234.5678", "new text")


class TestSlackDeleteMessage:
    async def test_delete_calls_api_with_correct_args(self, connector):
        connector._app = _make_mock_app()
        await connector.delete_message("C123", "1234.5678")
        connector._app.client.chat_delete.assert_called_once()
        call_kwargs = connector._app.client.chat_delete.call_args
        assert call_kwargs[1]["channel"] == "C123"
        assert call_kwargs[1]["ts"] == "1234.5678"

    async def test_delete_no_app_returns_early(self, connector):
        connector._app = None
        await connector.delete_message("C123", "1234.5678")


class TestSlackScheduleCleanup:
    async def test_creates_cleanup_task(self, connector):
        connector._app = _make_mock_app()
        connector.schedule_message_cleanup("C123", "1234.5678", delay=0.01)
        assert len(connector._cleanup_tasks) == 1


class TestSlackRequestApproval:
    async def test_sends_approval_with_buttons(self, connector):
        connector._app = _make_mock_app()
        ts = await connector.request_approval("C123", "apr-1", "Run `rm -rf /`", "Bash")
        assert ts == "1234.5678"
        call_kwargs = connector._app.client.chat_postMessage.call_args
        assert "blocks" in call_kwargs[1]
        blocks = call_kwargs[1]["blocks"]
        action_block = next(b for b in blocks if b["type"] == "actions")
        assert len(action_block["elements"]) == 3

    async def test_stores_tool_name(self, connector):
        connector._app = _make_mock_app()
        await connector.request_approval("C123", "apr-1", "desc", "Bash::rm")
        assert connector._approval_tool_names["apr-1"] == "Bash::rm"

    async def test_approve_all_label_uses_bash_cmd(self, connector):
        connector._app = _make_mock_app()
        await connector.request_approval("C123", "apr-1", "desc", "Bash::npm install")
        call_kwargs = connector._app.client.chat_postMessage.call_args
        blocks = call_kwargs[1]["blocks"]
        action_block = next(b for b in blocks if b["type"] == "actions")
        approve_all_btn = action_block["elements"][2]
        assert "npm install" in approve_all_btn["text"]["text"]

    async def test_approve_all_label_uses_tool_name(self, connector):
        connector._app = _make_mock_app()
        await connector.request_approval("C123", "apr-1", "desc", "Edit")
        call_kwargs = connector._app.client.chat_postMessage.call_args
        blocks = call_kwargs[1]["blocks"]
        action_block = next(b for b in blocks if b["type"] == "actions")
        approve_all_btn = action_block["elements"][2]
        assert "Edit" in approve_all_btn["text"]["text"]

    async def test_no_app_returns_none(self, connector):
        connector._app = None
        ts = await connector.request_approval("C123", "apr-1", "desc")
        assert ts is None


class TestSlackSendQuestion:
    async def test_sends_numbered_buttons(self, connector):
        connector._app = _make_mock_app()
        options = [{"label": "Alpha"}, {"label": "Beta"}]
        await connector.send_question("C123", "q-1", "Pick?", "Header", options)
        call_kwargs = connector._app.client.chat_postMessage.call_args
        blocks = call_kwargs[1]["blocks"]
        action_block = next(b for b in blocks if b["type"] == "actions")
        assert len(action_block["elements"]) == 2

    async def test_tracks_question_message_id(self, connector):
        connector._app = _make_mock_app()
        await connector.send_question("C123", "q-1", "?", "", [{"label": "A"}])
        assert "C123" in connector._question_message_ids
        assert connector._question_message_ids["C123"] == "1234.5678"


class TestSlackClearQuestionMessage:
    async def test_deletes_question_message(self, connector):
        connector._app = _make_mock_app()
        connector._question_message_ids["C123"] = "1234.5678"

        await connector.clear_question_message("C123")

        connector._app.client.chat_delete.assert_called_once()
        assert "C123" not in connector._question_message_ids

    async def test_clear_when_no_question(self, connector):
        connector._app = _make_mock_app()
        await connector.clear_question_message("C123")
        connector._app.client.chat_delete.assert_not_called()


class TestSlackSendPlanMessages:
    async def test_sends_chunks_and_collects_ids(self, connector):
        connector._app = _make_mock_app()
        long_plan = "a" * 5000
        ids = await connector.send_plan_messages("C123", long_plan)
        assert len(ids) == 2
        assert connector._plan_message_ids["C123"] == ids

    async def test_short_plan_single_message(self, connector):
        connector._app = _make_mock_app()
        ids = await connector.send_plan_messages("C123", "short plan")
        assert len(ids) == 1


class TestSlackClearPlanMessages:
    async def test_deletes_all_plan_messages(self, connector):
        connector._app = _make_mock_app()
        connector._plan_message_ids["C123"] = ["ts1", "ts2", "ts3"]

        await connector.clear_plan_messages("C123")

        assert connector._app.client.chat_delete.call_count == 3
        assert "C123" not in connector._plan_message_ids

    async def test_clear_when_no_plan_messages(self, connector):
        connector._app = _make_mock_app()
        await connector.clear_plan_messages("C123")
        connector._app.client.chat_delete.assert_not_called()


class TestSlackDeleteMessages:
    async def test_deletes_batch_and_cleans_state(self, connector):
        connector._app = _make_mock_app()
        connector._plan_message_ids["C123"] = ["ts1", "ts2"]

        await connector.delete_messages("C123", ["ts1", "ts2"])

        assert connector._app.client.chat_delete.call_count == 2
        assert "C123" not in connector._plan_message_ids


class TestSlackSendPlanReview:
    async def test_sends_four_buttons(self, connector):
        connector._app = _make_mock_app()
        await connector.send_plan_review("C123", "pr-1", "The plan text")
        calls = connector._app.client.chat_postMessage.call_args_list
        last_call = calls[-1]
        blocks = last_call[1]["blocks"]
        action_block = next(b for b in blocks if b["type"] == "actions")
        assert len(action_block["elements"]) == 4

    async def test_stores_plan_message_ids(self, connector):
        connector._app = _make_mock_app()
        await connector.send_plan_review("C123", "pr-1", "The plan text")
        assert "C123" in connector._plan_message_ids
        assert len(connector._plan_message_ids["C123"]) >= 2


class TestSlackSendFile:
    async def test_calls_upload(self, connector, tmp_path):
        connector._app = _make_mock_app()
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        await connector.send_file("C123", str(test_file))
        connector._app.client.files_upload_v2.assert_called_once()

    async def test_no_app_returns_early(self, connector, tmp_path):
        connector._app = None
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        await connector.send_file("C123", str(test_file))


class TestSlackSendInterruptPrompt:
    async def test_sends_two_buttons(self, connector):
        connector._app = _make_mock_app()
        ts = await connector.send_interrupt_prompt("C123", "int-1", "Preview text")
        assert ts == "1234.5678"
        call_kwargs = connector._app.client.chat_postMessage.call_args
        blocks = call_kwargs[1]["blocks"]
        action_block = next(b for b in blocks if b["type"] == "actions")
        assert len(action_block["elements"]) == 2
        labels = [e["text"]["text"] for e in action_block["elements"]]
        assert any("Send Now" in label for label in labels)
        assert any("Wait" in label for label in labels)

    async def test_truncates_long_preview(self, connector):
        connector._app = _make_mock_app()
        long_preview = "x" * 500
        await connector.send_interrupt_prompt("C123", "int-1", long_preview)
        call_kwargs = connector._app.client.chat_postMessage.call_args
        text = call_kwargs[1]["text"]
        # Preview should be truncated to 200 chars
        assert len(long_preview[:200]) == 200
        assert "x" * 200 in text

    async def test_no_app_returns_none(self, connector):
        connector._app = None
        ts = await connector.send_interrupt_prompt("C123", "int-1", "preview")
        assert ts is None


class TestSlackActivity:
    async def test_creates_activity(self, connector):
        connector._app = _make_mock_app()
        ts = await connector.send_activity("C123", "Bash", "npm install")
        assert ts == "1234.5678"
        assert connector._activity_message_id["C123"] == "1234.5678"

    async def test_updates_existing_activity(self, connector):
        connector._app = _make_mock_app()
        await connector.send_activity("C123", "Bash", "first")
        await connector.send_activity("C123", "Edit", "second")
        connector._app.client.chat_update.assert_called()

    async def test_dedup_same_text(self, connector):
        connector._app = _make_mock_app()
        await connector.send_activity("C123", "Bash", "same cmd")
        connector._app.client.chat_update.reset_mock()
        ts = await connector.send_activity("C123", "Bash", "same cmd")
        connector._app.client.chat_update.assert_not_called()
        assert ts == "1234.5678"

    async def test_clear_activity(self, connector):
        connector._app = _make_mock_app()
        await connector.send_activity("C123", "Bash", "cmd")
        await connector.clear_activity("C123")
        assert "C123" not in connector._activity_message_id
        assert "C123" not in connector._activity_last_text
        connector._app.client.chat_delete.assert_called_once()

    async def test_clear_activity_when_none(self, connector):
        connector._app = _make_mock_app()
        await connector.clear_activity("C123")
        connector._app.client.chat_delete.assert_not_called()

    async def test_no_app_returns_none(self, connector):
        connector._app = None
        ts = await connector.send_activity("C123", "Bash", "cmd")
        assert ts is None


class TestSlackActionRouting:
    async def test_approval_yes_with_correct_args(self, connector):
        connector._app = _make_mock_app()
        resolved = []

        async def resolver(aid, approved):
            resolved.append((aid, approved))
            return True

        connector.set_approval_resolver(resolver)
        connector._approval_tool_names["apr-1"] = "Bash"

        body = _make_action_body("approval:yes:apr-1")
        await connector._handle_action(body)
        assert len(resolved) == 1
        assert resolved[0] == ("apr-1", True)

    async def test_approval_no_button(self, connector):
        connector._app = _make_mock_app()
        resolved = []

        async def resolver(aid, approved):
            resolved.append((aid, approved))
            return True

        connector.set_approval_resolver(resolver)
        connector._approval_tool_names["apr-1"] = "Bash"

        body = _make_action_body("approval:no:apr-1")
        await connector._handle_action(body)
        assert len(resolved) == 1
        assert resolved[0] == ("apr-1", False)

    async def test_approval_all_triggers_auto_approve(self, connector):
        connector._app = _make_mock_app()
        auto_calls = []

        async def resolver(aid, approved):
            return True

        connector.set_approval_resolver(resolver)
        connector.set_auto_approve_handler(lambda cid, tn: auto_calls.append((cid, tn)))
        connector._approval_tool_names["apr-1"] = "Bash::rm"

        body = _make_action_body("approval:all:apr-1")
        await connector._handle_action(body)
        assert len(auto_calls) == 1
        assert auto_calls[0] == ("C123", "Bash::rm")

    async def test_expired_approval_updates_message(self, connector):
        connector._app = _make_mock_app()

        async def resolver(aid, approved):
            return False

        connector.set_approval_resolver(resolver)

        body = _make_action_body("approval:yes:apr-expired")
        await connector._handle_action(body)
        call_kwargs = connector._app.client.chat_update.call_args
        assert "Expired" in call_kwargs[1]["text"]

    async def test_approval_cleans_up_tool_name(self, connector):
        connector._app = _make_mock_app()

        async def resolver(aid, approved):
            return True

        connector.set_approval_resolver(resolver)
        connector._approval_tool_names["apr-1"] = "Bash"

        body = _make_action_body("approval:yes:apr-1")
        await connector._handle_action(body)
        assert "apr-1" not in connector._approval_tool_names

    async def test_interaction_button(self, connector):
        connector._app = _make_mock_app()
        resolved = []

        async def resolver(iid, answer):
            resolved.append((iid, answer))
            return True

        connector.set_interaction_resolver(resolver)

        body = _make_action_body("interact:q-1:Alpha")
        await connector._handle_action(body)
        assert len(resolved) == 1
        assert resolved[0] == ("q-1", "Alpha")

    async def test_interaction_plan_review_cleans_plan_messages(self, connector):
        connector._app = _make_mock_app()
        connector._plan_message_ids["C123"] = ["ts-plan-1", "ts-plan-2"]

        async def resolver(iid, answer):
            return True

        connector.set_interaction_resolver(resolver)

        body = _make_action_body("interact:pr-1:clean_edit")
        await connector._handle_action(body)
        # All plan messages + review message should be deleted
        assert connector._app.client.chat_delete.call_count >= 2
        assert "C123" not in connector._plan_message_ids

    async def test_interaction_question_cleans_question_message(self, connector):
        connector._app = _make_mock_app()
        connector._question_message_ids["C123"] = "ts-question"

        async def resolver(iid, answer):
            return True

        connector.set_interaction_resolver(resolver)

        body = _make_action_body("interact:q-1:Alpha")
        await connector._handle_action(body)
        assert "C123" not in connector._question_message_ids

    async def test_interrupt_send(self, connector):
        connector._app = _make_mock_app()
        resolved = []

        async def resolver(iid, send_now):
            resolved.append((iid, send_now))
            return True

        connector.set_interrupt_resolver(resolver)

        body = _make_action_body("interrupt:send:int-1")
        await connector._handle_action(body)
        assert len(resolved) == 1
        assert resolved[0] == ("int-1", True)

    async def test_interrupt_wait_decision(self, connector):
        connector._app = _make_mock_app()
        resolved = []

        async def resolver(iid, send_now):
            resolved.append((iid, send_now))
            return True

        connector.set_interrupt_resolver(resolver)

        body = _make_action_body("interrupt:wait:int-1")
        await connector._handle_action(body)
        assert len(resolved) == 1
        assert resolved[0] == ("int-1", False)

    async def test_malformed_action_no_colon_ignored(self, connector):
        connector._app = _make_mock_app()

        async def resolver(aid, approved):
            return True

        connector.set_approval_resolver(resolver)

        body = _make_action_body("approval:malformed")
        await connector._handle_action(body)

    async def test_empty_actions_list_ignored(self, connector):
        connector._app = _make_mock_app()
        body = {"actions": [], "channel": {"id": "C123"}, "message": {"ts": "1234"}}
        await connector._handle_action(body)


class TestSlackInboundMessage:
    async def test_routes_normal_message(self, connector):
        connector._app = _make_mock_app()
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return "ok"

        connector.set_message_handler(handler)

        event = {"user": "U123", "text": "hello", "channel": "C456"}
        await connector._handle_message_event(event)
        assert len(received) == 1
        assert received[0] == ("U123", "hello", "C456")

    async def test_ignores_bot_messages(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        event = {"user": "U123", "text": "hello", "channel": "C456", "bot_id": "B1"}
        await connector._handle_message_event(event)
        assert len(received) == 0

    async def test_ignores_message_changed_subtype(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        event = {
            "user": "U123",
            "text": "hello",
            "channel": "C456",
            "subtype": "message_changed",
        }
        await connector._handle_message_event(event)
        assert len(received) == 0

    async def test_missing_fields_ignored(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        await connector._handle_message_event(
            {"user": "U123", "text": "", "channel": "C"}
        )
        await connector._handle_message_event(
            {"user": "", "text": "hi", "channel": "C"}
        )
        await connector._handle_message_event(
            {"user": "U123", "text": "hi", "channel": ""}
        )
        assert len(received) == 0

    async def test_routes_commands(self, connector):
        connector._app = _make_mock_app()
        commands = []

        async def handler(uid, cmd, args, cid):
            commands.append((cmd, args))
            return "done"

        connector.set_command_handler(handler)

        event = {"user": "U123", "text": "/status check", "channel": "C456"}
        await connector._handle_message_event(event)
        assert commands[0] == ("status", "check")

    async def test_command_handler_error_does_not_crash(self, connector):
        connector._app = _make_mock_app()

        async def bad_handler(uid, cmd, args, cid):
            raise RuntimeError("boom")

        connector.set_command_handler(bad_handler)

        event = {"user": "U123", "text": "/status", "channel": "C456"}
        await connector._handle_message_event(event)

    async def test_message_handler_exception_sends_error(self, connector):
        connector._app = _make_mock_app()

        async def bad_handler(uid, text, cid):
            raise RuntimeError("boom")

        connector.set_message_handler(bad_handler)

        event = {"user": "U123", "text": "hello", "channel": "C456"}
        await connector._handle_message_event(event)
        connector._app.client.chat_postMessage.assert_called()
        last_call = connector._app.client.chat_postMessage.call_args
        assert "error occurred" in last_call[1]["text"].lower()

    async def test_no_handler_set_does_not_crash(self, connector):
        event = {"user": "U123", "text": "hello", "channel": "C456"}
        await connector._handle_message_event(event)


@pytest.mark.usefixtures("_mock_slack_deps")
class TestSlackBlockKitHelpers:
    def test_button_truncates_text(self):
        from leashd.connectors.slack import _button

        btn = _button("a" * 100, "action-id")
        assert len(btn["text"]["text"]) == 75

    def test_button_truncates_action_id(self):
        from leashd.connectors.slack import _button

        btn = _button("text", "a" * 300)
        assert len(btn["action_id"]) == 255

    def test_button_preserves_short_values(self):
        from leashd.connectors.slack import _button

        btn = _button("OK", "ok-action")
        assert btn["text"]["text"] == "OK"
        assert btn["action_id"] == "ok-action"
        assert btn["type"] == "button"

    def test_text_with_buttons_layout(self):
        from leashd.connectors.slack import _text_with_buttons

        buttons = [
            [InlineButton(text="A", callback_data="a")],
            [InlineButton(text="B", callback_data="b")],
        ]
        blocks = _text_with_buttons("message text", buttons)
        assert blocks[0]["type"] == "section"
        assert blocks[0]["text"]["text"] == "message text"
        assert blocks[1]["type"] == "actions"
        assert len(blocks[1]["elements"]) == 2

    def test_text_with_buttons_max_25(self):
        from leashd.connectors.slack import _text_with_buttons

        buttons = [
            [InlineButton(text=f"B{i}", callback_data=f"b{i}")] for i in range(30)
        ]
        blocks = _text_with_buttons("text", buttons)
        action_block = blocks[1]
        assert len(action_block["elements"]) == 25

    def test_text_with_buttons_truncates_text(self):
        from leashd.connectors.slack import _text_with_buttons

        long_text = "x" * 4000
        buttons = [[InlineButton(text="A", callback_data="a")]]
        blocks = _text_with_buttons(long_text, buttons)
        assert len(blocks[0]["text"]["text"]) == 3000
