"""Engine tests — what a chat's conversations look like after a daemon restart.

A restart drops every pane and every in-process dict; the session store is the
only thing that crosses it. These pin down what a user gets back.
"""

import pytest

from leashd.connectors.telegram_sessions import ChatSessionRouter
from leashd.core.engine import Engine
from leashd.core.session import SessionManager
from leashd.storage.sqlite import SqliteSessionStore
from tests.conftest import MockConnector
from tests.core.engine.conftest import FakeAgent


class ChatAwareAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.live: set[str] = set()

    def live_chat_ids(self) -> set[str]:
        return set(self.live)

    async def cancel_chat(self, chat_id: str) -> None:
        self.live.discard(chat_id)


@pytest.fixture
async def store(tmp_path):
    store = SqliteSessionStore(tmp_path / "sessions.db")
    await store.setup()
    yield store
    await store.teardown()


@pytest.fixture
def build(config, policy_engine, audit_logger, store):
    """Build an engine on the shared store — call twice to span a restart."""

    def _build():
        connector = MockConnector(chat_sessions=True, support_streaming=True)
        agent = ChatAwareAgent()
        engine = Engine(
            connector=connector,
            agent=agent,
            config=config,
            session_manager=SessionManager(store=store),
            policy_engine=policy_engine,
            audit=audit_logger,
            store=store,
        )
        return engine, connector, agent

    return _build


def _last(connector):
    return connector.sent_messages[-1]


class TestRestart:
    async def test_the_roster_comes_back_whole(self, build):
        before, _, _ = build()
        await before.handle_command("u1", "session", "new", "chat1")
        await before.handle_message("u1", "hello from two", "chat1:s2")

        after, connector, _ = build()
        await after.handle_command("u1", "session", "", "chat1")

        text = _last(connector)["text"]
        assert "#1 · " in text
        assert "#2 · " in text

    async def test_each_conversation_keeps_its_own_resume_token(self, build, store):
        before, _, _ = build()
        await before.handle_command("u1", "session", "new", "chat1")
        await before.handle_message("u1", "first", "chat1")
        await before.handle_message("u1", "second", "chat1:s2")

        after, _, _ = build()
        primary = await after.session_manager.get_or_create("u1", "chat1", "")
        secondary = await after.session_manager.get_or_create("u1", "chat1:s2", "")

        assert primary.agent_resume_token == "test-session-123"
        assert secondary.agent_resume_token == "test-session-123"
        assert primary.session_id != secondary.session_id

    async def test_every_conversation_reports_no_agent(self, build):
        """This runtime has no panes to hand back, so nothing is live yet."""
        before, _, _ = build()
        await before.handle_command("u1", "session", "new", "chat1")

        after, connector, _ = build()
        await after.handle_command("u1", "session", "", "chat1")

        assert _last(connector)["text"].count("no agent") == 2

    async def test_switching_back_replays_where_the_conversation_left_off(self, build):
        before, _, _ = build()
        await before.handle_command("u1", "session", "new", "chat1")
        await before.handle_message("u1", "what did we decide", "chat1:s2")

        after, connector, _ = build()
        await after.handle_command("u1", "session", "2", "chat1")

        assert "Echo: what did we decide" in _last(connector)["text"]


class TestForegroundAfterRestart:
    """Which conversation the chat is showing has to outlive the process."""

    async def test_the_chat_reattaches_to_the_conversation_it_was_showing(
        self, build, store
    ):
        before, _, _ = build()
        await before.handle_command("u1", "session", "new", "chat1")
        assert store is not None

        after, connector, _ = build()
        await after.startup()

        assert connector.activated_chat_sessions == ["chat1:s2"]

    async def test_switching_back_to_slot_one_is_not_restored(self, build):
        before, _, _ = build()
        await before.handle_command("u1", "session", "new", "chat1")
        await before.handle_command("u1", "session", "1", "chat1:s2")

        after, connector, _ = build()
        await after.startup()

        assert connector.activated_chat_sessions == []

    async def test_a_chat_that_never_split_is_left_alone(self, build):
        before, _, _ = build()
        await before.handle_message("u1", "hello", "chat1")

        after, connector, _ = build()
        await after.startup()

        assert connector.activated_chat_sessions == []

    async def test_a_terminated_conversation_is_not_restored(self, build):
        before, _, _ = build()
        await before.handle_command("u1", "session", "new", "chat1")
        await before.handle_command("u1", "session", "kill 2", "chat1:s2")

        after, connector, _ = build()
        await after.startup()

        assert connector.activated_chat_sessions == []

    async def test_the_router_routes_the_next_message_to_the_restored_slot(self):
        router = ChatSessionRouter()
        router.activate("284184690:s2")

        assert router.inbound("284184690") == "284184690:s2"
        assert router.is_foreground("284184690:s2") is True
        assert router.is_foreground("284184690") is False
