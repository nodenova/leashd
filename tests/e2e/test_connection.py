"""E2E tests for the WebUI connection indicator."""

import pytest
from playwright.async_api import Page

from tests.e2e.conftest import SimpleNamespace, inject


@pytest.mark.e2e
class TestConnectionIndicator:
    async def test_connection_dot_shows_connected(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        dot_class = await authed_page.get_attribute("#connection-dot", "class")
        assert "dot-connected" in (dot_class or "")

    async def test_pong_keeps_connected_state(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "pong",
            {},
        )
        dot_class = await authed_page.get_attribute("#connection-dot", "class")
        assert "dot-connected" in (dot_class or "")
        assert "unstable" not in (dot_class or "")
