"""Tests for the Signal connector."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def _mock_httpx():
    """Patch httpx before importing SignalConnector."""
    mock_client_cls = MagicMock()
    with patch.dict(
        "sys.modules",
        {"httpx": MagicMock(AsyncClient=mock_client_cls)},
    ):
        yield mock_client_cls


@pytest.fixture
def connector(_mock_httpx):
    from leashd.connectors.signal_connector import SignalConnector

    return SignalConnector(
        phone_number="+15551234567",
        cli_url="http://localhost:8080",
    )


def _make_mock_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(status_code=200))
    client.get = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: []))
    client.put = AsyncMock(return_value=MagicMock(status_code=200))
    client.aclose = AsyncMock()
    return client


def _make_envelope(text, source_number="+15559876543", group_id=None, source=None):
    """Helper to build a signal-cli envelope."""
    envelope_inner = {}
    if source_number:
        envelope_inner["sourceNumber"] = source_number
    if source:
        envelope_inner["source"] = source

    data_msg = {"message": text}
    if group_id:
        data_msg["groupInfo"] = {"groupId": group_id}
    envelope_inner["dataMessage"] = data_msg

    return {"envelope": envelope_inner}


class TestSignalLifecycle:
    async def test_stop_cancels_receive_task(self, connector):
        cancelled = False

        async def fake_task():
            nonlocal cancelled
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                cancelled = True
                raise

        task = asyncio.create_task(fake_task())
        connector._receive_task = task
        connector._client = _make_mock_client()

        await connector.stop()

        assert task.cancelled() or cancelled

    async def test_stop_closes_client(self, connector):
        client = _make_mock_client()
        connector._client = client
        connector._receive_task = None

        await connector.stop()

        client.aclose.assert_called_once()
        assert connector._client is None

    async def test_stop_when_not_started(self, connector):
        assert connector._client is None
        assert connector._receive_task is None
        await connector.stop()


class TestSignalSendMessage:
    async def test_sends_to_recipient(self, connector):
        connector._client = _make_mock_client()
        await connector.send_message("+15559876543", "hello")
        connector._client.post.assert_called()
        call_kwargs = connector._client.post.call_args
        assert call_kwargs[1]["json"]["recipients"] == ["+15559876543"]
        assert call_kwargs[1]["json"]["message"] == "hello"

    async def test_splits_long_message(self, connector):
        connector._client = _make_mock_client()
        long_text = "a" * 2500
        await connector.send_message("+15559876543", long_text)
        assert connector._client.post.call_count == 2

    async def test_no_client_returns_early(self, connector):
        connector._client = None
        connector._pending_approval["+15559876543"] = "should-not-change"
        await connector.send_message("+15559876543", "hello")
        assert connector._pending_approval["+15559876543"] == "should-not-change"


class TestSignalTypingIndicator:
    async def test_sends_typing(self, connector):
        connector._client = _make_mock_client()
        await connector.send_typing_indicator("+15559876543")
        connector._client.put.assert_called_once()
        call_args = connector._client.put.call_args
        assert "/typing-indicator/" in call_args[0][0]

    async def test_no_client_returns_early(self, connector):
        connector._client = None
        await connector.send_typing_indicator("+15559876543")


class TestSignalSendFile:
    async def test_sends_base64_encoded_file(self, connector, tmp_path):
        connector._client = _make_mock_client()
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello file content")

        await connector.send_file("+15559876543", str(test_file))

        connector._client.post.assert_called()
        call_kwargs = connector._client.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["recipients"] == ["+15559876543"]
        expected_b64 = base64.b64encode(b"hello file content").decode()
        assert payload["base64_attachments"] == [expected_b64]

    async def test_send_file_no_client(self, connector):
        connector._client = None
        await connector.send_file("+15559876543", "/nonexistent/file.txt")


class TestSignalRequestApproval:
    async def test_sends_text_approval(self, connector):
        connector._client = _make_mock_client()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        result = await connector.request_approval(
            "+15559876543", "apr-1", "Run rm -rf /"
        )
        assert len(sent) == 1
        assert "APPROVAL REQUIRED" in sent[0]
        assert connector._pending_approval["+15559876543"] == "apr-1"
        assert result is None


class TestSignalSendQuestion:
    async def test_sends_numbered_options(self, connector):
        connector._client = _make_mock_client()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        options = [{"label": "Alpha"}, {"label": "Beta"}]
        await connector.send_question("+15559876543", "q-1", "Pick?", "Header", options)
        assert "1. Alpha" in sent[0]
        assert "2. Beta" in sent[0]

    async def test_stores_pending_interaction_state(self, connector):
        connector._client = _make_mock_client()
        connector.send_message = AsyncMock()
        options = [{"label": "X"}, {"label": "Y"}]

        await connector.send_question("+15559876543", "q-1", "?", "H", options)

        assert "+15559876543" in connector._pending_interaction
        iid, stored_opts = connector._pending_interaction["+15559876543"]
        assert iid == "q-1"
        assert stored_opts == options


class TestSignalSendPlanReview:
    async def test_sends_plan_with_options(self, connector):
        connector._client = _make_mock_client()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        await connector.send_plan_review("+15559876543", "pr-1", "The plan")
        assert "The plan" in sent[0]
        assert connector._pending_plan_review["+15559876543"] == "pr-1"


class TestSignalInbound:
    async def test_routes_normal_message(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return "ok"

        connector.set_message_handler(handler)

        envelope = _make_envelope("hello")
        await connector._handle_envelope(envelope)
        assert len(received) == 1
        assert received[0][0] == "+15559876543"
        assert received[0][1] == "hello"

    async def test_ignores_empty_data_message(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        envelope = {"envelope": {"sourceNumber": "+15559876543"}}
        await connector._handle_envelope(envelope)
        assert len(received) == 0

    async def test_ignores_empty_text(self, connector):
        """Empty message text should not reach the handler."""
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        envelope = _make_envelope("")
        await connector._handle_envelope(envelope)
        assert len(received) == 0

    async def test_no_source_returns_early(self, connector):
        """Envelope without sourceNumber or source should be dropped."""
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        envelope = {"envelope": {"dataMessage": {"message": "hello"}}}
        await connector._handle_envelope(envelope)
        assert len(received) == 0

    async def test_source_fallback_field(self, connector):
        """When sourceNumber is missing, falls back to 'source' field."""
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return ""

        connector.set_message_handler(handler)

        envelope = _make_envelope("hello", source_number=None, source="+15550001111")
        await connector._handle_envelope(envelope)
        assert len(received) == 1
        assert received[0][0] == "+15550001111"

    async def test_resolves_approval_and_cleans_state(self, connector):
        resolved = []

        async def resolver(aid, approved):
            resolved.append((aid, approved))
            return True

        connector.set_approval_resolver(resolver)
        connector._pending_approval["+15559876543"] = "apr-1"

        envelope = _make_envelope("approve")
        await connector._handle_envelope(envelope)
        assert len(resolved) == 1
        assert resolved[0] == ("apr-1", True)
        assert "+15559876543" not in connector._pending_approval

    async def test_resolves_rejection_and_cleans_state(self, connector):
        resolved = []

        async def resolver(aid, approved):
            resolved.append((aid, approved))
            return True

        connector.set_approval_resolver(resolver)
        connector._pending_approval["+15559876543"] = "apr-1"

        envelope = _make_envelope("no")
        await connector._handle_envelope(envelope)
        assert len(resolved) == 1
        assert resolved[0] == ("apr-1", False)
        assert "+15559876543" not in connector._pending_approval

    async def test_approve_all_triggers_auto_approve_handler(self, connector):
        auto_approve_calls = []

        async def resolver(aid, approved):
            return True

        connector.set_approval_resolver(resolver)
        connector.set_auto_approve_handler(
            lambda cid, tn: auto_approve_calls.append((cid, tn))
        )
        connector._pending_approval["+15559876543"] = "apr-1"

        envelope = _make_envelope("approve-all")
        await connector._handle_envelope(envelope)
        assert len(auto_approve_calls) == 1
        assert auto_approve_calls[0] == ("+15559876543", "")

    async def test_resolves_interaction_and_cleans_state(self, connector):
        resolved = []

        async def resolver(iid, answer):
            resolved.append((iid, answer))
            return True

        connector.set_interaction_resolver(resolver)
        connector._pending_interaction["+15559876543"] = (
            "q-1",
            [{"label": "Alpha"}, {"label": "Beta"}],
        )

        envelope = _make_envelope("2")
        await connector._handle_envelope(envelope)
        assert len(resolved) == 1
        assert resolved[0] == ("q-1", "Beta")
        assert "+15559876543" not in connector._pending_interaction

    async def test_resolves_plan_review_and_cleans_state(self, connector):
        resolved = []

        async def resolver(iid, answer):
            resolved.append((iid, answer))
            return True

        connector.set_interaction_resolver(resolver)
        connector._pending_plan_review["+15559876543"] = "pr-1"

        envelope = _make_envelope("3")
        await connector._handle_envelope(envelope)
        assert len(resolved) == 1
        assert resolved[0] == ("pr-1", "default")
        assert "+15559876543" not in connector._pending_plan_review

    async def test_group_message_uses_group_id(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return ""

        connector.set_message_handler(handler)

        envelope = _make_envelope("hi group", group_id="group123")
        await connector._handle_envelope(envelope)
        assert received[0][2] == "group123"

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

        envelope = _make_envelope("/dir projects")
        await connector._handle_envelope(envelope)
        assert commands[0] == ("dir", "projects")
        assert "done" in sent

    async def test_command_handler_error_does_not_crash(self, connector):
        async def bad_handler(uid, cmd, args, cid):
            raise RuntimeError("boom")

        connector.set_command_handler(bad_handler)
        connector.send_message = AsyncMock()

        envelope = _make_envelope("/status")
        await connector._handle_envelope(envelope)

    async def test_message_handler_exception_sends_error_reply(self, connector):
        async def bad_handler(uid, text, cid):
            raise RuntimeError("boom")

        connector.set_message_handler(bad_handler)
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        envelope = _make_envelope("hello")
        await connector._handle_envelope(envelope)
        assert len(sent) == 1
        assert "error occurred" in sent[0].lower()

    async def test_no_handler_set_does_not_crash(self, connector):
        envelope = _make_envelope("hello")
        await connector._handle_envelope(envelope)

    async def test_normal_message_not_routed_as_approval_when_no_pending(
        self, connector
    ):
        """'approve' text should reach message handler when no approval is pending."""
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        envelope = _make_envelope("approve")
        await connector._handle_envelope(envelope)
        assert len(received) == 1
        assert received[0] == "approve"
