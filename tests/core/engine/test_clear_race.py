"""Tests for /clear race conditions with active agent execution.

Validates that /clear during active agent execution properly:
- Marks the chat as interrupted so _execute_turn skips update_from_result
- Prevents the old agent result from corrupting the reset session
- Supports the full interrupt → clear → new message flow
"""

import asyncio

import pytest

from leashd.agents.base import AgentResponse, BaseAgent
from leashd.core.engine import Engine
from leashd.core.session import SessionManager


class SlowAgent(BaseAgent):
    """Agent that blocks until released, allowing race condition testing.

    ``release_on_cancel=False`` keeps the turn parked past the whole ``/clear``
    handler, so a test can pin down what a turn returning *after* the reset
    does rather than racing the reset for it.
    """

    def __init__(self, release_on_cancel: bool = True):
        self.execute_entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.execute_count = 0
        self.release_on_cancel = release_on_cancel

    async def execute(self, prompt, session, *, can_use_tool=None, **kwargs):
        self.execute_count += 1
        self.execute_entered.set()
        await self.release.wait()
        return AgentResponse(
            content=f"Echo: {prompt}",
            session_id="old-agent-session-id",
            cost=0.05,
        )

    async def cancel(self, session_id):
        self.cancelled = True
        # Release the blocking agent so _execute_turn completes
        if self.release_on_cancel:
            self.release.set()

    async def shutdown(self):
        pass


class TestClearDuringExecution:
    async def test_clear_marks_interrupted_when_agent_active(
        self, config, audit_logger, policy_engine
    ):
        """When agent is executing, /clear should add chat to _interrupted_chats."""
        agent = SlowAgent()
        eng = Engine(
            connector=None,
            agent=agent,
            config=config,
            session_manager=SessionManager(),
            policy_engine=policy_engine,
            audit=audit_logger,
        )

        # Start agent execution in background
        task = asyncio.create_task(eng.handle_message("user1", "hello", "chat1"))
        await agent.execute_entered.wait()

        # Now clear while agent is running
        await eng.handle_command("user1", "clear", "", "chat1")

        assert agent.cancelled
        # The release was triggered by cancel, so the task should complete
        await task

        # Session should be freshly reset, not corrupted by old agent result
        session = eng.session_manager.get("user1", "chat1")
        assert session is not None
        assert session.agent_resume_token is None
        assert session.message_count == 0
        assert session.total_cost == 0.0

    async def test_clear_prevents_old_result_from_overwriting_session(
        self, config, audit_logger, policy_engine
    ):
        """Old agent result must not write session_id back after /clear resets it."""
        agent = SlowAgent()
        eng = Engine(
            connector=None,
            agent=agent,
            config=config,
            session_manager=SessionManager(),
            policy_engine=policy_engine,
            audit=audit_logger,
        )

        task = asyncio.create_task(eng.handle_message("user1", "hello", "chat1"))
        await agent.execute_entered.wait()

        # Capture session_id before clear
        session = eng.session_manager.get("user1", "chat1")
        pre_clear_id = session.session_id

        await eng.handle_command("user1", "clear", "", "chat1")
        await task

        # After clear + agent return, session should have a NEW id from reset
        session = eng.session_manager.get("user1", "chat1")
        assert session.session_id != pre_clear_id
        # The old agent's session_id ("old-agent-session-id") must NOT be persisted
        assert session.agent_resume_token is None

    async def test_interrupt_then_clear_then_new_message(
        self, config, audit_logger, policy_engine
    ):
        """Full flow: message → interrupt → /clear → new message starts fresh."""
        agent = SlowAgent()
        eng = Engine(
            connector=None,
            agent=agent,
            config=config,
            session_manager=SessionManager(),
            policy_engine=policy_engine,
            audit=audit_logger,
        )

        # First message — agent blocks
        task = asyncio.create_task(eng.handle_message("user1", "first", "chat1"))
        await agent.execute_entered.wait()

        # Clear resets and cancels
        await eng.handle_command("user1", "clear", "", "chat1")
        await task

        # Prepare agent for second message
        agent.execute_entered.clear()
        agent.release.clear()
        agent.cancelled = False
        agent.execute_count = 0

        # Second message should start fresh — release immediately
        agent.release.set()
        result = await eng.handle_message("user1", "second", "chat1")

        assert "Echo: second" in result
        session = eng.session_manager.get("user1", "chat1")
        assert session.message_count == 1
        assert session.total_cost == pytest.approx(0.05)

    async def test_clear_cleans_up_executing_state(
        self, config, audit_logger, policy_engine
    ):
        """After clear + agent return, _executing_chats should be clean."""
        agent = SlowAgent()
        eng = Engine(
            connector=None,
            agent=agent,
            config=config,
            session_manager=SessionManager(),
            policy_engine=policy_engine,
            audit=audit_logger,
        )

        task = asyncio.create_task(eng.handle_message("user1", "hello", "chat1"))
        await agent.execute_entered.wait()

        await eng.handle_command("user1", "clear", "", "chat1")
        await task

        assert "chat1" not in eng._executing_chats
        assert "chat1" not in eng._interrupted_chats
        assert "chat1" not in eng._executing_sessions

    async def test_clear_discards_queued_messages(
        self, config, audit_logger, policy_engine
    ):
        """/clear should drop queued messages so they aren't processed after reset."""
        agent = SlowAgent()
        eng = Engine(
            connector=None,
            agent=agent,
            config=config,
            session_manager=SessionManager(),
            policy_engine=policy_engine,
            audit=audit_logger,
        )

        task = asyncio.create_task(eng.handle_message("user1", "first", "chat1"))
        await agent.execute_entered.wait()

        # Queue a message while agent is running
        await eng.handle_message("user1", "queued msg", "chat1")
        assert len(eng._pending_messages.get("chat1", [])) == 1

        # Clear should discard queued messages
        await eng.handle_command("user1", "clear", "", "chat1")
        await task

        assert "chat1" not in eng._pending_messages


class TestClearWithConnector:
    async def test_clear_discards_turn_returning_after_reset(
        self, config, audit_logger, policy_engine, mock_connector
    ):
        """A turn that lands after /clear writes nothing into the fresh chat.

        ``/clear`` swaps a new ``session_id`` in under the running turn, so the
        turn is discarded on return rather than reported: the user already has
        "Session cleared", and the transient "Task interrupted." belongs to
        ``/stop`` and ``/cancel``, which leave the session in place.
        """
        mock_connector._support_streaming = True
        agent = SlowAgent(release_on_cancel=False)
        eng = Engine(
            connector=mock_connector,
            agent=agent,
            config=config,
            session_manager=SessionManager(),
            policy_engine=policy_engine,
            audit=audit_logger,
        )

        task = asyncio.create_task(eng.handle_message("user1", "hello", "chat1"))
        await agent.execute_entered.wait()

        reply = await eng.handle_command("user1", "clear", "", "chat1")
        assert agent.cancelled
        assert "cleared" in reply.lower()

        agent.release.set()
        assert await task == ""

        texts = [m.get("text", "") for m in mock_connector.sent_messages]
        assert not any("Echo: hello" in t for t in texts)
        assert not any("interrupted" in t.lower() for t in texts)

    async def test_clear_reports_interrupt_when_turn_lands_before_reset(
        self, config, audit_logger, policy_engine, mock_connector
    ):
        """A turn that lands on the cancel, before the reset, is reported.

        The interrupt marker ``/clear`` sets is consumed first here, so the
        turn takes the ordinary interrupted path — either way, nothing the
        agent produced reaches the cleared conversation.
        """
        mock_connector._support_streaming = True
        agent = SlowAgent()
        eng = Engine(
            connector=mock_connector,
            agent=agent,
            config=config,
            session_manager=SessionManager(),
            policy_engine=policy_engine,
            audit=audit_logger,
        )

        task = asyncio.create_task(eng.handle_message("user1", "hello", "chat1"))
        await agent.execute_entered.wait()

        eng._interrupted_chats.add("chat1")
        await eng.agent.cancel(eng._executing_sessions["chat1"])
        assert await task == ""

        await eng.handle_command("user1", "clear", "", "chat1")

        texts = [m.get("text", "") for m in mock_connector.sent_messages]
        assert not any("Echo: hello" in t for t in texts)
        assert any("Task interrupted" in t for t in texts)
