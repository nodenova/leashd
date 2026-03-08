"""Tests for the WhatsApp connector (OpenClaw bridge)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def _mock_ws_deps():
    """Patch websockets imports before importing WhatsAppConnector."""
    mock_connect = AsyncMock()
    with patch.dict(
        "sys.modules",
        {
            "websockets": MagicMock(),
            "websockets.asyncio": MagicMock(),
            "websockets.asyncio.client": MagicMock(connect=mock_connect),
        },
    ):
        yield mock_connect


def _make_mock_ws():
    """Create a mock WebSocket with send/recv/close."""
    ws = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def connector(_mock_ws_deps):
    from leashd.connectors.whatsapp import WhatsAppConnector

    return WhatsAppConnector(
        gateway_url="ws://localhost:18789",
        gateway_token="test-token",
        phone_number="+15551234567",
    )


def _make_inbound(text, session_key="whatsapp:+15559876543:dm", role="user"):
    """Helper to build a WhatsApp inbound payload."""
    return {
        "message": {"role": role, "content": text},
        "sessionKey": session_key,
    }


class TestWhatsAppLifecycle:
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
        connector._ws = _make_mock_ws()

        await connector.stop()

        assert task.cancelled() or cancelled

    async def test_stop_closes_websocket(self, connector):
        ws = _make_mock_ws()
        connector._ws = ws
        connector._receive_task = None

        await connector.stop()

        ws.close.assert_called_once()
        assert connector._ws is None

    async def test_stop_when_not_started(self, connector):
        assert connector._ws is None
        assert connector._receive_task is None
        await connector.stop()


class TestWhatsAppConnect:
    async def test_connect_raises_on_wrong_challenge(self, connector):
        from leashd.exceptions import ConnectorError

        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(return_value=json.dumps({"event": "wrong.event"}))

        from unittest.mock import patch as _patch

        with (
            _patch(
                "leashd.connectors.whatsapp.ws_connect",
                new=AsyncMock(return_value=mock_ws),
            ),
            pytest.raises(ConnectorError, match=r"Expected connect\.challenge"),
        ):
            await connector._connect()

    async def test_connect_raises_on_failed_handshake(self, connector):
        from leashd.exceptions import ConnectorError

        mock_ws = _make_mock_ws()
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps({"event": "connect.challenge"}),
                json.dumps({"ok": False, "error": "bad token"}),
            ]
        )

        from unittest.mock import patch as _patch

        with (
            _patch(
                "leashd.connectors.whatsapp.ws_connect",
                new=AsyncMock(return_value=mock_ws),
            ),
            pytest.raises(ConnectorError, match="handshake failed"),
        ):
            await connector._connect()


class TestWhatsAppRPC:
    async def test_rpc_raises_when_not_connected(self, connector):
        from leashd.exceptions import ConnectorError

        connector._ws = None
        with pytest.raises(ConnectorError, match="not connected"):
            await connector._rpc("send", {"to": "chat-1", "message": "hi"})

    async def test_rpc_sends_json_message(self, connector):
        ws = _make_mock_ws()
        connector._ws = ws

        # Resolve the future immediately from a concurrent task
        async def fake_send(data):
            frame = json.loads(data)
            req_id = frame["id"]
            future = connector._pending_rpc.get(req_id)
            if future and not future.done():
                future.set_result({"ok": True})

        ws.send = fake_send

        result = await connector._rpc("send", {"to": "c", "message": "m"})
        assert result == {"ok": True}

    async def test_next_request_id_increments(self, connector):
        id1 = connector._next_request_id()
        id2 = connector._next_request_id()
        id3 = connector._next_request_id()
        assert int(id1) < int(id2) < int(id3)


class TestWhatsAppSendMessage:
    async def test_sends_via_rpc(self, connector):
        connector._ws = _make_mock_ws()

        rpc_calls = []

        async def mock_rpc(method, params):
            rpc_calls.append((method, params))
            return {"ok": True}

        connector._rpc = mock_rpc

        await connector.send_message("chat-1", "hello")
        assert len(rpc_calls) == 1
        assert rpc_calls[0][0] == "send"
        assert rpc_calls[0][1]["message"] == "hello"
        assert rpc_calls[0][1]["to"] == "chat-1"
        assert rpc_calls[0][1]["channel"] == "whatsapp"

    async def test_splits_long_message(self, connector):
        connector._ws = _make_mock_ws()
        rpc_calls = []

        async def mock_rpc(method, params):
            rpc_calls.append((method, params))
            return {"ok": True}

        connector._rpc = mock_rpc

        long_text = "a" * 5000
        await connector.send_message("chat-1", long_text)
        assert len(rpc_calls) == 2

    async def test_send_message_exception_logged_not_raised(self, connector):
        async def bad_rpc(method, params):
            raise RuntimeError("connection lost")

        connector._rpc = bad_rpc

        await connector.send_message("chat-1", "hello")


class TestWhatsAppSendFile:
    async def test_sends_media_url(self, connector):
        connector._ws = _make_mock_ws()
        rpc_calls = []

        async def mock_rpc(method, params):
            rpc_calls.append((method, params))
            return {"ok": True}

        connector._rpc = mock_rpc

        await connector.send_file("chat-1", "/path/to/file.png")
        assert len(rpc_calls) == 1
        assert rpc_calls[0][1]["mediaUrl"] == "/path/to/file.png"

    async def test_send_file_exception_logged_not_raised(self, connector):
        async def bad_rpc(method, params):
            raise RuntimeError("connection lost")

        connector._rpc = bad_rpc

        await connector.send_file("chat-1", "/path/to/file.png")


class TestWhatsAppRequestApproval:
    async def test_sends_text_approval(self, connector):
        connector._ws = _make_mock_ws()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        await connector.request_approval("chat-1", "apr-1", "Run rm -rf /")
        assert len(sent) == 1
        assert "APPROVAL REQUIRED" in sent[0]
        assert connector._pending_approval["chat-1"] == "apr-1"


class TestWhatsAppSendQuestion:
    async def test_sends_numbered_options(self, connector):
        connector._ws = _make_mock_ws()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        options = [{"label": "Alpha"}, {"label": "Beta"}]
        await connector.send_question("chat-1", "q-1", "Pick?", "Header", options)
        assert "1. Alpha" in sent[0]
        assert "2. Beta" in sent[0]
        assert connector._pending_interaction["chat-1"][0] == "q-1"

    async def test_stores_pending_interaction_state(self, connector):
        connector.send_message = AsyncMock()
        options = [{"label": "X"}, {"label": "Y"}]

        await connector.send_question("chat-1", "q-1", "?", "H", options)

        iid, stored_opts = connector._pending_interaction["chat-1"]
        assert iid == "q-1"
        assert stored_opts == options


class TestWhatsAppSendPlanReview:
    async def test_sends_plan_with_options(self, connector):
        connector._ws = _make_mock_ws()
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        await connector.send_plan_review("chat-1", "pr-1", "The plan")
        assert "The plan" in sent[0]
        assert connector._pending_plan_review["chat-1"] == "pr-1"


class TestWhatsAppTypingIndicator:
    async def test_typing_is_noop_no_rpc_called(self, connector):
        rpc_calls = []

        async def mock_rpc(method, params):
            rpc_calls.append(method)
            return {}

        connector._rpc = mock_rpc
        await connector.send_typing_indicator("chat-1")
        assert len(rpc_calls) == 0


class TestWhatsAppInbound:
    async def test_routes_normal_message(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append((uid, text, cid))
            return "ok"

        connector.set_message_handler(handler)

        payload = _make_inbound("hello")
        await connector._handle_inbound(payload)
        assert len(received) == 1
        assert received[0][0] == "+15559876543"
        assert received[0][1] == "hello"

    async def test_ignores_assistant_messages(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        payload = _make_inbound("response", role="assistant")
        await connector._handle_inbound(payload)
        assert len(received) == 0

    async def test_ignores_non_dict_message(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        payload = {"message": "just a string", "sessionKey": "whatsapp:+155:dm"}
        await connector._handle_inbound(payload)
        assert len(received) == 0

    async def test_ignores_empty_text(self, connector):
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        payload = _make_inbound("")
        await connector._handle_inbound(payload)
        assert len(received) == 0

    async def test_resolves_approval_and_cleans_state(self, connector):
        resolved = []

        async def resolver(aid, approved):
            resolved.append((aid, approved))
            return True

        connector.set_approval_resolver(resolver)
        connector._pending_approval["whatsapp:+15559876543:dm"] = "apr-1"

        payload = _make_inbound("approve")
        await connector._handle_inbound(payload)
        assert len(resolved) == 1
        assert resolved[0] == ("apr-1", True)
        assert "whatsapp:+15559876543:dm" not in connector._pending_approval

    async def test_resolves_rejection_and_cleans_state(self, connector):
        resolved = []

        async def resolver(aid, approved):
            resolved.append((aid, approved))
            return True

        connector.set_approval_resolver(resolver)
        connector._pending_approval["whatsapp:+15559876543:dm"] = "apr-1"

        payload = _make_inbound("reject")
        await connector._handle_inbound(payload)
        assert len(resolved) == 1
        assert resolved[0] == ("apr-1", False)
        assert "whatsapp:+15559876543:dm" not in connector._pending_approval

    async def test_approve_all_triggers_auto_approve_handler(self, connector):
        auto_approve_calls = []

        async def resolver(aid, approved):
            return True

        connector.set_approval_resolver(resolver)
        connector.set_auto_approve_handler(
            lambda cid, tn: auto_approve_calls.append((cid, tn))
        )
        connector._pending_approval["whatsapp:+15559876543:dm"] = "apr-1"

        payload = _make_inbound("approve-all")
        await connector._handle_inbound(payload)
        assert len(auto_approve_calls) == 1
        assert auto_approve_calls[0] == ("whatsapp:+15559876543:dm", "")

    async def test_resolves_interaction_and_cleans_state(self, connector):
        resolved = []

        async def resolver(iid, answer):
            resolved.append((iid, answer))
            return True

        connector.set_interaction_resolver(resolver)
        connector._pending_interaction["whatsapp:+15559876543:dm"] = (
            "q-1",
            [{"label": "Alpha"}, {"label": "Beta"}],
        )

        payload = _make_inbound("2")
        await connector._handle_inbound(payload)
        assert len(resolved) == 1
        assert resolved[0] == ("q-1", "Beta")
        assert "whatsapp:+15559876543:dm" not in connector._pending_interaction

    async def test_resolves_plan_review_and_cleans_state(self, connector):
        resolved = []

        async def resolver(iid, answer):
            resolved.append((iid, answer))
            return True

        connector.set_interaction_resolver(resolver)
        connector._pending_plan_review["whatsapp:+15559876543:dm"] = "pr-1"

        payload = _make_inbound("1")
        await connector._handle_inbound(payload)
        assert len(resolved) == 1
        assert resolved[0] == ("pr-1", "clean_edit")
        assert "whatsapp:+15559876543:dm" not in connector._pending_plan_review

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

        payload = _make_inbound("/status check")
        await connector._handle_inbound(payload)
        assert commands[0] == ("status", "check")
        assert "done" in sent

    async def test_command_handler_error_does_not_crash(self, connector):
        async def bad_handler(uid, cmd, args, cid):
            raise RuntimeError("boom")

        connector.set_command_handler(bad_handler)
        connector.send_message = AsyncMock()

        payload = _make_inbound("/status")
        await connector._handle_inbound(payload)

    async def test_message_handler_exception_sends_error_reply(self, connector):
        async def bad_handler(uid, text, cid):
            raise RuntimeError("boom")

        connector.set_message_handler(bad_handler)
        sent = []

        async def mock_send(cid, text, **kw):
            sent.append(text)

        connector.send_message = mock_send

        payload = _make_inbound("hello")
        await connector._handle_inbound(payload)
        assert len(sent) == 1
        assert "error occurred" in sent[0].lower()

    async def test_no_handler_set_does_not_crash(self, connector):
        payload = _make_inbound("hello")
        await connector._handle_inbound(payload)

    async def test_normal_message_not_routed_as_approval_when_no_pending(
        self, connector
    ):
        """'approve' text should reach message handler when no approval is pending."""
        received = []

        async def handler(uid, text, cid):
            received.append(text)
            return ""

        connector.set_message_handler(handler)

        payload = _make_inbound("approve")
        await connector._handle_inbound(payload)
        assert len(received) == 1
        assert received[0] == "approve"
