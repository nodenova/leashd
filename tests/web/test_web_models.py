"""Tests for leashd.web.models — WebSocket message types."""

import pytest
from pydantic import ValidationError

from leashd.web.models import ClientMessage, ServerMessage


class TestClientMessage:
    def test_valid_auth_message(self):
        msg = ClientMessage(type="auth", payload={"api_key": "test"})
        assert msg.type == "auth"
        assert msg.payload["api_key"] == "test"

    def test_valid_message_type(self):
        msg = ClientMessage(type="message", payload={"text": "hello"})
        assert msg.type == "message"

    def test_valid_ping(self):
        msg = ClientMessage(type="ping")
        assert msg.type == "ping"
        assert msg.payload == {}

    def test_valid_approval_response(self):
        msg = ClientMessage(
            type="approval_response",
            payload={"approval_id": "ap-1", "approved": True},
        )
        assert msg.type == "approval_response"
        assert msg.payload["approved"] is True

    def test_valid_interaction_response(self):
        msg = ClientMessage(
            type="interaction_response",
            payload={"interaction_id": "int-1", "answer": "option_a"},
        )
        assert msg.type == "interaction_response"

    def test_valid_interrupt_response(self):
        msg = ClientMessage(
            type="interrupt_response",
            payload={"interrupt_id": "irq-1", "send_now": False},
        )
        assert msg.type == "interrupt_response"
        assert msg.payload["send_now"] is False

    def test_frozen_rejects_mutation(self):
        msg = ClientMessage(type="ping")
        with pytest.raises(ValidationError):
            msg.type = "pong"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ClientMessage(type="invalid_type")

    def test_empty_type_rejected(self):
        with pytest.raises(ValidationError):
            ClientMessage(type="")

    def test_serialization_roundtrip(self):
        msg = ClientMessage(
            type="approval_response", payload={"id": "1", "approved": True}
        )
        data = msg.model_dump()
        restored = ClientMessage.model_validate(data)
        assert restored == msg

    @pytest.mark.parametrize(
        "msg_type",
        [
            "auth",
            "message",
            "approval_response",
            "interaction_response",
            "interrupt_response",
            "ping",
        ],
    )
    def test_all_client_message_types_valid(self, msg_type):
        msg = ClientMessage(type=msg_type)
        assert msg.type == msg_type


class TestServerMessage:
    def test_valid_auth_ok(self):
        msg = ServerMessage(type="auth_ok", payload={"session_id": "abc"})
        assert msg.type == "auth_ok"

    def test_valid_stream_token(self):
        msg = ServerMessage(
            type="stream_token", payload={"text": "hello", "message_id": "1"}
        )
        assert msg.type == "stream_token"

    def test_valid_error(self):
        msg = ServerMessage(type="error", payload={"reason": "bad"})
        assert msg.type == "error"

    def test_valid_message(self):
        msg = ServerMessage(type="message", payload={"text": "hello"})
        assert msg.type == "message"

    def test_valid_approval_request(self):
        msg = ServerMessage(
            type="approval_request",
            payload={"request_id": "ap-1", "tool": "Bash", "description": "ls"},
        )
        assert msg.type == "approval_request"

    def test_valid_question(self):
        msg = ServerMessage(
            type="question",
            payload={
                "interaction_id": "int-1",
                "question": "Which?",
                "options": [{"label": "A"}],
            },
        )
        assert msg.type == "question"

    def test_valid_plan_review(self):
        msg = ServerMessage(
            type="plan_review",
            payload={"interaction_id": "int-1", "description": "Plan desc"},
        )
        assert msg.type == "plan_review"

    def test_valid_interrupt_prompt(self):
        msg = ServerMessage(
            type="interrupt_prompt",
            payload={"interrupt_id": "irq-1", "message_preview": "preview"},
        )
        assert msg.type == "interrupt_prompt"

    def test_valid_tool_start(self):
        msg = ServerMessage(
            type="tool_start",
            payload={"tool": "Bash", "command": "ls", "message_id": "1"},
        )
        assert msg.type == "tool_start"

    def test_valid_tool_end(self):
        msg = ServerMessage(type="tool_end")
        assert msg.type == "tool_end"
        assert msg.payload == {}

    def test_valid_message_delete(self):
        msg = ServerMessage(type="message_delete", payload={"message_id": "1"})
        assert msg.type == "message_delete"

    def test_valid_task_update(self):
        msg = ServerMessage(
            type="task_update",
            payload={"phase": "plan", "status": "running", "description": "Planning"},
        )
        assert msg.type == "task_update"

    def test_valid_reload(self):
        msg = ServerMessage(type="reload")
        assert msg.type == "reload"
        assert msg.payload == {}

    def test_valid_config_updated(self):
        msg = ServerMessage(type="config_updated")
        assert msg.type == "config_updated"

    def test_frozen_rejects_mutation(self):
        msg = ServerMessage(type="pong")
        with pytest.raises(ValidationError):
            msg.type = "ping"

    def test_json_serialization(self):
        msg = ServerMessage(type="status", payload={"typing": True})
        json_str = msg.model_dump_json()
        assert "status" in json_str
        assert "typing" in json_str

    @pytest.mark.parametrize(
        "msg_type",
        [
            "auth_ok",
            "auth_error",
            "history",
            "message",
            "stream_token",
            "tool_start",
            "tool_end",
            "approval_request",
            "approval_resolved",
            "message_complete",
            "message_delete",
            "question",
            "plan_review",
            "interrupt_prompt",
            "task_update",
            "error",
            "pong",
            "status",
            "reload",
            "config_updated",
        ],
    )
    def test_all_server_message_types_valid(self, msg_type):
        msg = ServerMessage(type=msg_type)
        assert msg.type == msg_type

    def test_invalid_server_type_rejected(self):
        with pytest.raises(ValidationError):
            ServerMessage(type="nonexistent_type")
