"""The Telegram chat-session router — foreground state and id translation."""

from leashd.connectors.telegram_sessions import ChatSessionRouter


class TestInbound:
    def test_a_fresh_chat_is_attached_to_its_primary_conversation(self):
        router = ChatSessionRouter()
        assert router.inbound("284184690") == "284184690"

    def test_inbound_follows_the_activated_slot(self):
        router = ChatSessionRouter()
        router.activate("284184690:s2")
        assert router.inbound("284184690") == "284184690:s2"

    def test_activating_the_primary_clears_the_override(self):
        router = ChatSessionRouter()
        router.activate("284184690:s2")
        router.activate("284184690")
        assert router.inbound("284184690") == "284184690"

    def test_chats_have_independent_foregrounds(self):
        router = ChatSessionRouter()
        router.activate("111:s2")
        assert router.inbound("111") == "111:s2"
        assert router.inbound("222") == "222"

    def test_activate_returns_the_owning_chat(self):
        router = ChatSessionRouter()
        assert router.activate("284184690:s3") == "284184690"


class TestOutbound:
    def test_every_slot_targets_the_same_telegram_chat(self):
        router = ChatSessionRouter()
        assert router.target("284184690") == "284184690"
        assert router.target("284184690:s4") == "284184690"

    def test_only_the_activated_slot_is_foreground(self):
        router = ChatSessionRouter()
        router.activate("284184690:s2")
        assert router.is_foreground("284184690:s2") is True
        assert router.is_foreground("284184690") is False
        assert router.is_foreground("284184690:s3") is False

    def test_primary_is_foreground_by_default(self):
        router = ChatSessionRouter()
        assert router.is_foreground("284184690") is True
        assert router.is_foreground("284184690:s2") is False

    def test_forget_falls_back_to_the_primary(self):
        router = ChatSessionRouter()
        router.activate("284184690:s2")
        router.forget("284184690:s2")
        assert router.inbound("284184690") == "284184690"

    def test_forget_ignores_a_slot_that_is_not_foreground(self):
        router = ChatSessionRouter()
        router.activate("284184690:s2")
        router.forget("284184690:s3")
        assert router.inbound("284184690") == "284184690:s2"
