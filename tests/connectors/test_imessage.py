"""Tests for the iMessage connector (BlueBubbles)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def _mock_httpx():
    """Patch httpx before importing IMessageConnector."""
    mock_client_cls = MagicMock()
    with patch.dict(
        "sys.modules",
        {"httpx": MagicMock(AsyncClient=mock_client_cls)},
    ):
        yield mock_client_cls


@pytest.fixture
def connector(_mock_httpx):
    from leashd.connectors.imessage import IMessageConnector

    return IMessageConnector(
        server_url="http://localhost:1234",
        password="test-password",
    )


def _make_mock_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=200))
    client.get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=lambda: {"data": {"os_version": "15.0", "server_version": "1.0"}},
            raise_for_status=MagicMock(),
        )
    )
    client.aclose = AsyncMock()
    return client


CHAT_ID = "iMessage;-;+15559876543"


def _make_message(
    text,
    sender_address="+15559876543",
    chat_guid=CHAT_ID,
    is_from_me=False,
    date_created=1000001,
    handle_id=None,
    chat_identifier=None,
    chats=None,
    handle=None,
):
    """Helper to build an iMessage payload."""
    msg = {
        "isFromMe": is_from_me,
        "text": text,
        "dateCreated": date_created,
    }

    if handle is not None:
        msg["handle"] = handle
    elif handle_id:
        msg["handle"] = {"id": handle_id}
    elif sender_address:
        msg["handle"] = {"address": sender_address}
    else:
        msg["handle"] = {}

    if chats is not None:
        msg["chats"] = chats
    elif chat_identifier:
        msg["chats"] = [{"chatIdentifier": chat_identifier}]
    elif chat_guid:
        msg["chats"] = [{"guid": chat_guid}]
    else:
        msg["chats"] = []

    return msg


class TestIMessageLifecycle:
    async def test_stop_cancels_poll_task(self, connector):
        cancelled = False

        async def fake_task():
            nonlocal cancelled
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                cancelled = True
                raise

        task = asyncio.create_task(fake_task())
        connector._poll_task = task
        connector._client = _make_mock_client()

        await connector.stop()

        assert task.cancelled() or cancelled

    async def test_stop_closes_client(self, connector):
        client = _make_mock_client()
        connector._client = client
        connector._poll_task = None

        await connector.stop()

        client.aclose.assert_called_once()
        assert connector._client is None

    async def test_stop_when_not_started(self, connector):
        assert connector._client is None
        assert connector._poll_task is None
        await connector.stop()


class TestIMessageSendMessage:
    async def test_sends_to_chat_guid(self, connector):
        connector._client = _make_mock_client()
        await connector.send_message(CHAT_ID, "hello")
        connector._client.post.assert_called()
        call_kwargs = connector._client.post.call_args
        assert call_kwargs[1]["json"]["chatGuid"] == CHAT_ID
        assert call_kwargs[1]["json"]["message"] == "hello"

    async def test_splits_long_message(self, connector):
        connector._client = _make_mock_client()
        long_text = "a" * 6000
        await connector.send_message("chat-1", long_text)
        assert connector._client.post.call_count == 2

    async def test_no_client_returns_early(self, connector):
        connector._client = None
        await connector.send_message("chat-1", "hello")


class TestIMessageTypingIndicator:
    async def test_sends_typing(self, connector):
        connector._client = _make_mock_client()
        await connector.send_typing_indicator(CHAT_ID)
        connector._client.post.assert_called_once()
        call_args = connector._client.post.call_args
        assert "/typing" in call_args[0][0]

    async def test_no_client_returns_early(self, connector):
        connector._client = None
        await connector.send_typing_indicator("chat-1")


class TestIMessageSendFile:
    async def test_sends_file_as_multipart(self, connector, tmp_path):
        connector._client = _make_mock_client()
        test_file = tmp_path / "doc.txt"
        test_file.write_text("file content")

        await connector.send_file(CHAT_ID, str(test_file))

        connector._client.post.assert_called()
        call_args = connector._client.post.call_args
        assert "/attachment" in call_args[0][0]
        assert call_args[1]["data"]["chatGuid"] == CHAT_ID

    async def test_send_file_no_client(self, connector):
        connector._client = None
        await connector.send_file(CHAT_ID, "/nonexistent/file.txt")


class TestIMessageRequestApproval:
    async def test_sends_text_approval(self, connector):
        connector._client = _make_mock_client()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        result = await connector.request_approval(CHAT_ID, "apr-1", "Run rm -rf /")
        assert len(sent) == 1
        assert "APPROVAL REQUIRED" in sent[0]
        assert connector._pending_approval[CHAT_ID] == "apr-1"
        assert result is None


class TestIMessageSendQuestion:
    async def test_sends_numbered_options(self, connector):
        connector._client = _make_mock_client()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        options = [{"label": "Alpha"}, {"label": "Beta"}]
        await connector.send_question(CHAT_ID, "q-1", "Pick?", "Header", options)
        assert "1. Alpha" in sent[0]
        assert "2. Beta" in sent[0]

    async def test_stores_pending_interaction_state(self, connector):
        connector.send_message = AsyncMock()
        options = [{"label": "X"}, {"label": "Y"}]

        await connector.send_question(CHAT_ID, "q-1", "?", "H", options)

        assert CHAT_ID in connector._pending_interaction
        iid, stored_opts = connector._pending_interaction[CHAT_ID]
        assert iid == "q-1"
        assert stored_opts == options


class TestIMessageSendPlanReview:
    async def test_sends_plan_with_options(self, connector):
        connector._client = _make_mock_client()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        await connector.send_plan_review(CHAT_ID, "pr-1", "The plan")
        assert "The plan" in sent[0]
        assert connector._pending_plan_review[CHAT_ID] == "pr-1"


class TestIMessageInbound:
    async def test_routes_normal_message(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return "ok"

        connector.set_message_handler(handler)

        msg = _make_message("hello")
        await connector._handle_message(msg)
        assert len(received) == 1
        assert received[0][0] == "+15559876543"
        assert received[0][1] == "hello"
        assert received[0][2] == CHAT_ID

    async def test_ignores_outgoing_messages(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        msg = _make_message("my reply", is_from_me=True)
        await connector._handle_message(msg)
        assert len(received) == 0

    async def test_ignores_empty_text(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        msg = _make_message("")
        await connector._handle_message(msg)
        assert len(received) == 0

    async def test_missing_sender_returns_early(self, connector):
        """Message with empty handle should be dropped."""
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        msg = _make_message("hello", handle={})
        await connector._handle_message(msg)
        assert len(received) == 0

    async def test_handle_fallback_to_id(self, connector):
        """When address is missing, sender extracted from handle.id."""
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text))
            return ""

        connector.set_message_handler(handler)

        msg = _make_message("hello", handle_id="user@icloud.com", sender_address=None)
        await connector._handle_message(msg)
        assert len(received) == 1
        assert received[0][0] == "user@icloud.com"

    async def test_handle_string_handles(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return ""

        connector.set_message_handler(handler)

        msg = _make_message("hello", handle="+15559876543")
        await connector._handle_message(msg)
        assert received[0][0] == "+15559876543"

    async def test_chat_id_falls_back_to_chat_identifier(self, connector):
        """When guid is missing, falls back to chatIdentifier."""
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return ""

        connector.set_message_handler(handler)

        msg = _make_message("hello", chat_guid=None, chat_identifier="foo@bar.com")
        await connector._handle_message(msg)
        assert len(received) == 1
        assert received[0][2] == "foo@bar.com"

    async def test_chat_id_falls_back_to_sender(self, connector):
        """When chats list is empty, chat_id should equal the sender."""
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return ""

        connector.set_message_handler(handler)

        msg = _make_message("hello", chats=[])
        await connector._handle_message(msg)
        assert len(received) == 1
        assert received[0][2] == "+15559876543"

    async def test_resolves_approval_and_cleans_state(self, connector):
        resolved = []

        async def resolver(aid, approved):
            resolved.append((aid, approved))
            return True

        connector.set_approval_resolver(resolver)
        connector._pending_approval[CHAT_ID] = "apr-1"

        msg = _make_message("approve")
        await connector._handle_message(msg)
        assert len(resolved) == 1
        assert resolved[0] == ("apr-1", True)
        assert CHAT_ID not in connector._pending_approval

    async def test_resolves_rejection_and_cleans_state(self, connector):
        resolved = []

        async def resolver(aid, approved):
            resolved.append((aid, approved))
            return True

        connector.set_approval_resolver(resolver)
        connector._pending_approval[CHAT_ID] = "apr-1"

        msg = _make_message("reject")
        await connector._handle_message(msg)
        assert len(resolved) == 1
        assert resolved[0] == ("apr-1", False)
        assert CHAT_ID not in connector._pending_approval

    async def test_approve_all_triggers_auto_approve_handler(self, connector):
        auto_approve_calls = []

        async def resolver(aid, approved):
            return True

        connector.set_approval_resolver(resolver)
        connector.set_auto_approve_handler(
            lambda cid, tn: auto_approve_calls.append((cid, tn))
        )
        connector._pending_approval[CHAT_ID] = "apr-1"

        msg = _make_message("approve-all")
        await connector._handle_message(msg)
        assert len(auto_approve_calls) == 1
        assert auto_approve_calls[0] == (CHAT_ID, "")

    async def test_resolves_interaction_and_cleans_state(self, connector):
        resolved = []

        async def resolver(iid, answer):
            resolved.append((iid, answer))
            return True

        connector.set_interaction_resolver(resolver)
        connector._pending_interaction[CHAT_ID] = (
            "q-1",
            [{"label": "Alpha"}, {"label": "Beta"}],
        )

        msg = _make_message("2")
        await connector._handle_message(msg)
        assert len(resolved) == 1
        assert resolved[0] == ("q-1", "Beta")
        assert CHAT_ID not in connector._pending_interaction

    async def test_resolves_plan_review_and_cleans_state(self, connector):
        resolved = []

        async def resolver(iid, answer):
            resolved.append((iid, answer))
            return True

        connector.set_interaction_resolver(resolver)
        connector._pending_plan_review[CHAT_ID] = "pr-1"

        msg = _make_message("4")
        await connector._handle_message(msg)
        assert len(resolved) == 1
        assert resolved[0] == ("pr-1", "adjust")
        assert CHAT_ID not in connector._pending_plan_review

    async def test_routes_commands(self, connector):
        commands = []

        async def handler(uid, cmd, args, cid):
            commands.append((cmd, args))
            return "done"

        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.set_command_handler(handler)
        connector.send_message = mock_send

        msg = _make_message("/status")
        await connector._handle_message(msg)
        assert commands[0] == ("status", "")
        assert "done" in sent

    async def test_command_handler_error_does_not_crash(self, connector):
        async def bad_handler(uid, cmd, args, cid):
            raise RuntimeError("boom")

        connector.set_command_handler(bad_handler)
        connector.send_message = AsyncMock()

        msg = _make_message("/status")
        await connector._handle_message(msg)

    async def test_message_handler_exception_sends_error_reply(self, connector):
        async def bad_handler(uid, text, cid):
            raise RuntimeError("boom")

        connector.set_message_handler(bad_handler)
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        msg = _make_message("hello")
        await connector._handle_message(msg)
        assert len(sent) == 1
        assert "error occurred" in sent[0].lower()

    async def test_no_handler_set_does_not_crash(self, connector):
        msg = _make_message("hello")
        await connector._handle_message(msg)

    async def test_normal_message_not_routed_as_approval_when_no_pending(
        self, connector
    ):
        """'approve' text should reach message handler when no approval is pending."""
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        msg = _make_message("approve")
        await connector._handle_message(msg)
        assert len(received) == 1
        assert received[0] == "approve"
