"""Regression: /stop must halt an in-flight agent retry loop.

Before the fix, when the user sent /stop while the agent was executing,
`Engine._cleanup_session` called `agent.cancel(session_id)` (SIGTERM on the
Claude CLI subprocess). The retry loop in each runtime caught the
exception from the killed subprocess and — because `session.agent_resume_token`
was still set — spawned a brand-new subprocess, silently ignoring the stop.
This test pins the fix: after /stop, no second `execute` call is made.
"""

import asyncio

from leashd.agents.base import BaseAgent
from leashd.core.engine import Engine
from leashd.core.session import SessionManager
from leashd.exceptions import AgentError


class RetryingAgent(BaseAgent):
    """Fake agent that simulates the buggy retry-after-cancel scenario.

    On first execute it blocks until cancel() is called, then raises a
    SIGTERM-ish exception. A correct implementation must NOT call execute
    again; the broken implementation would retry.
    """

    def __init__(self):
        self.execute_entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.execute_count = 0
        self._cancelled_sessions: set[str] = set()

    async def execute(self, prompt, session, *, can_use_tool=None, **kwargs):
        self.execute_count += 1
        self.execute_entered.set()
        await self.cancelled.wait()
        # Simulate the retry loop's behavior: honor the cancellation flag
        # and raise an AgentError instead of silently retrying.
        if session.session_id in self._cancelled_sessions:
            raise AgentError("Execution cancelled by user")
        raise AgentError("CLI exited with code 143")

    async def cancel(self, session_id):
        self._cancelled_sessions.add(session_id)
        self.cancelled.set()

    async def shutdown(self):
        pass


class TestStopDuringRetry:
    async def test_stop_does_not_spawn_fresh_execution(
        self, config, audit_logger, policy_engine
    ):
        """The canonical bug: /stop arrives mid-execution, agent must not retry."""
        agent = RetryingAgent()
        eng = Engine(
            connector=None,
            agent=agent,
            config=config,
            session_manager=SessionManager(),
            policy_engine=policy_engine,
            audit=audit_logger,
        )

        # Start the agent; it will block until cancel() fires.
        task = asyncio.create_task(eng.handle_message("user1", "hello", "chat1"))
        await agent.execute_entered.wait()

        # Simulate a stale resume token — this is what fooled the old retry
        # loop into spawning a fresh subprocess after SIGTERM.
        session = eng.session_manager.get("user1", "chat1")
        assert session is not None
        session.agent_resume_token = "stale-resume-token"

        # User hits /stop.
        await eng.handle_command("user1", "stop", "", "chat1")

        # Wait for the handle_message coroutine to unwind.
        await task

        # The critical invariant: execute was called exactly once.
        # If the retry loop bypassed cancellation, execute_count would be >= 2.
        assert agent.execute_count == 1, (
            f"agent.execute should have been called once, "
            f"got {agent.execute_count} — retry after cancel regression"
        )

    async def test_stop_clears_resume_token(self, config, audit_logger, policy_engine):
        """After /stop the session must not carry a stale resume token."""
        agent = RetryingAgent()
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
        session = eng.session_manager.get("user1", "chat1")
        session.agent_resume_token = "stale-resume-token"

        await eng.handle_command("user1", "stop", "", "chat1")
        await task

        session = eng.session_manager.get("user1", "chat1")
        assert session.agent_resume_token is None
