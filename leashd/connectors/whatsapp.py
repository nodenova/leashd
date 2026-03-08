"""WhatsApp connector — OpenClaw gateway bridge over WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

import structlog

from leashd.connectors._shared import (
    format_text_approval,
    format_text_plan_review,
    format_text_question,
    parse_text_response,
    split_text,
)
from leashd.connectors.base import BaseConnector, InlineButton
from leashd.exceptions import ConnectorError

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError as exc:
    raise ImportError(
        "WhatsApp connector requires websockets. "
        "Install with: uv pip install 'leashd[whatsapp]'"
    ) from exc

logger = structlog.get_logger()

_MAX_MESSAGE_LENGTH = 4000
_RECONNECT_MAX_DELAY = 30.0
_RECONNECT_BASE_DELAY = 1.0


class WhatsAppConnector(BaseConnector):
    """WhatsApp connector using OpenClaw gateway WebSocket bridge."""

    def __init__(self, gateway_url: str, gateway_token: str, phone_number: str) -> None:
        super().__init__()
        self._gateway_url = gateway_url
        self._gateway_token = gateway_token
        self._phone_number = phone_number

        self._ws: Any = None
        self._receive_task: asyncio.Task[None] | None = None
        self._request_id = 0
        self._pending_rpc: dict[str, asyncio.Future[dict[str, Any]]] = {}

        # Text-based fallback state
        self._pending_approval: dict[str, str] = {}  # chat_id → approval_id
        self._pending_interaction: dict[str, tuple[str, list[dict[str, str]]]] = {}
        self._pending_plan_review: dict[str, str] = {}  # chat_id → interaction_id

    # --- Lifecycle ---

    async def start(self) -> None:
        await self._connect()
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("whatsapp_connector_started")

    async def stop(self) -> None:
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("whatsapp_connector_stopped")

    async def _connect(self) -> None:
        self._ws = await ws_connect(self._gateway_url)

        # Wait for challenge
        raw = await self._ws.recv()
        challenge = json.loads(raw)
        if challenge.get("event") != "connect.challenge":
            msg = f"Expected connect.challenge, got: {challenge.get('event')}"
            raise ConnectorError(msg)

        # Send auth handshake
        req_id = self._next_request_id()
        handshake = {
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {
                "minProtocol": 1,
                "maxProtocol": 1,
                "client": {
                    "id": f"leashd-{uuid.uuid4().hex[:8]}",
                    "version": "0.7.0",
                    "platform": "python",
                    "mode": "operator",
                },
                "auth": {"token": self._gateway_token},
            },
        }
        await self._ws.send(json.dumps(handshake))

        # Wait for hello-ok
        raw = await self._ws.recv()
        resp = json.loads(raw)
        if not resp.get("ok"):
            msg = f"Gateway handshake failed: {resp}"
            raise ConnectorError(msg)

        logger.info("whatsapp_gateway_connected")

    def _next_request_id(self) -> str:
        self._request_id += 1
        return str(self._request_id)

    # --- RPC ---

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._ws:
            msg = "WhatsApp gateway not connected"
            raise ConnectorError(msg)

        req_id = self._next_request_id()
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_event_loop().create_future()
        )
        self._pending_rpc[req_id] = future

        msg = json.dumps(
            {"type": "req", "id": req_id, "method": method, "params": params}
        )
        await self._ws.send(msg)

        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except TimeoutError:
            self._pending_rpc.pop(req_id, None)
            raise
        finally:
            self._pending_rpc.pop(req_id, None)

    # --- Messaging ---

    async def send_message(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[InlineButton]] | None = None,  # noqa: ARG002
    ) -> None:
        chunks = split_text(text, _MAX_MESSAGE_LENGTH)
        for chunk in chunks:
            try:
                await self._rpc(
                    "send", {"to": chat_id, "message": chunk, "channel": "whatsapp"}
                )
            except Exception:
                logger.exception("whatsapp_send_message_failed", chat_id=chat_id)

    async def send_typing_indicator(self, chat_id: str) -> None:
        """Not available via OpenClaw gateway — no-op."""

    async def send_file(self, chat_id: str, file_path: str) -> None:
        try:
            await self._rpc(
                "send",
                {"to": chat_id, "mediaUrl": file_path, "channel": "whatsapp"},
            )
        except Exception:
            logger.exception("whatsapp_send_file_failed", chat_id=chat_id)

    # --- Approvals (text-based fallback) ---

    async def request_approval(
        self,
        chat_id: str,
        approval_id: str,
        description: str,
        tool_name: str = "",  # noqa: ARG002
    ) -> str | None:
        text = format_text_approval(description)
        self._pending_approval[chat_id] = approval_id
        await self.send_message(chat_id, text)
        logger.info(
            "whatsapp_approval_requested", chat_id=chat_id, approval_id=approval_id
        )
        return None

    # --- Questions (text-based fallback) ---

    async def send_question(
        self,
        chat_id: str,
        interaction_id: str,
        question_text: str,
        header: str,
        options: list[dict[str, str]],
    ) -> None:
        text = format_text_question(question_text, header, options)
        self._pending_interaction[chat_id] = (interaction_id, options)
        await self.send_message(chat_id, text)

    # --- Plan review (text-based fallback) ---

    async def send_plan_review(
        self, chat_id: str, interaction_id: str, description: str
    ) -> None:
        text = format_text_plan_review(description)
        self._pending_plan_review[chat_id] = interaction_id
        await self.send_message(chat_id, text)

    # --- Inbound receive loop ---

    async def _receive_loop(self) -> None:
        reconnect_delay = _RECONNECT_BASE_DELAY

        while True:
            try:
                async for raw in self._ws:
                    frame = json.loads(raw)
                    frame_type = frame.get("type")

                    # RPC response
                    if frame_type == "res":
                        req_id = frame.get("id")
                        future = self._pending_rpc.pop(str(req_id), None)
                        if future and not future.done():
                            future.set_result(frame)
                        continue

                    # Inbound event
                    if frame_type == "event" and frame.get("event") == "chat.event":
                        await self._handle_inbound(frame.get("payload", {}))
                        continue

            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning(
                    "whatsapp_ws_disconnected",
                    reconnect_delay=reconnect_delay,
                )

            # Reconnect with backoff
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, _RECONNECT_MAX_DELAY)

            try:
                await self._connect()
                reconnect_delay = _RECONNECT_BASE_DELAY
            except Exception:
                logger.exception("whatsapp_reconnect_failed")

    async def _handle_inbound(self, payload: dict[str, Any]) -> None:
        message = payload.get("message", {})
        if not isinstance(message, dict):
            return

        # Only process user messages
        role = message.get("role", "")
        if role != "user":
            return

        text = message.get("content", "") or message.get("text", "")
        if not text or not isinstance(text, str):
            return

        session_key = payload.get("sessionKey", "")
        # Extract sender from session key (format: whatsapp:+phone:...)
        parts = session_key.split(":")
        sender = parts[1] if len(parts) >= 2 else session_key
        chat_id = session_key or sender

        # Check text-based responses for pending states
        parsed = parse_text_response(
            text,
            has_pending_approval=chat_id in self._pending_approval,
            has_pending_interaction=chat_id in self._pending_interaction,
            pending_options=(
                self._pending_interaction[chat_id][1]
                if chat_id in self._pending_interaction
                else None
            ),
            has_pending_plan_review=chat_id in self._pending_plan_review,
        )

        if parsed:
            if parsed.kind == "approval" and self._approval_resolver:
                approval_id = self._pending_approval.pop(chat_id, "")
                if approval_id:
                    approved = parsed.value in ("approve", "approve-all")
                    await self._approval_resolver(approval_id, approved)
                    if parsed.value == "approve-all" and self._auto_approve_handler:
                        self._auto_approve_handler(chat_id, "")
                return

            if parsed.kind == "interaction" and self._interaction_resolver:
                iid, _opts = self._pending_interaction.pop(chat_id, ("", []))
                if iid:
                    await self._interaction_resolver(iid, parsed.value)
                return

            if parsed.kind == "plan_review" and self._interaction_resolver:
                iid = self._pending_plan_review.pop(chat_id, "")
                if iid:
                    await self._interaction_resolver(iid, parsed.value)
                return

        # Route /commands
        if text.startswith("/") and self._command_handler:
            cmd_parts = text.split(None, 1)
            command = cmd_parts[0].lstrip("/")
            args = cmd_parts[1] if len(cmd_parts) > 1 else ""
            try:
                result = await self._command_handler(sender, command, args, chat_id)
                if result:
                    await self.send_message(chat_id, result)
            except Exception:
                logger.exception("whatsapp_command_handler_error", chat_id=chat_id)
            return

        # Normal message
        if self._message_handler:
            try:
                result = await self._message_handler(sender, text, chat_id)
                if result == "":
                    pass  # Engine handled via streaming
            except Exception:
                logger.exception("whatsapp_message_handler_error", chat_id=chat_id)
                await self.send_message(
                    chat_id, "An error occurred while processing your message."
                )
