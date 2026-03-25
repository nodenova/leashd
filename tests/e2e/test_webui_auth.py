"""E2E tests for the WebUI authentication flow."""

import asyncio

import pytest
from playwright.async_api import Page

from tests.e2e.conftest import SimpleNamespace


@pytest.mark.e2e
class TestAuthFlow:
    async def test_valid_key_shows_chat_screen(
        self, page: Page, test_server: SimpleNamespace
    ) -> None:
        await page.goto(test_server.url)
        await page.fill("#api-key-input", test_server.api_key)
        await page.click("#auth-btn")

        await page.wait_for_selector("#chat-screen:not([hidden])", timeout=6000)
        assert await page.is_hidden("#auth-screen")
        await page.wait_for_selector(".dot-connected", timeout=6000)

    async def test_wrong_key_shows_error(
        self, page: Page, test_server: SimpleNamespace
    ) -> None:
        await page.goto(test_server.url)
        await page.fill("#api-key-input", "wrong-key")
        await page.click("#auth-btn")

        await page.wait_for_selector("#auth-error:not([hidden])", timeout=6000)
        error_text = await page.text_content("#auth-error")
        assert error_text  # non-empty error message
        assert await page.is_hidden("#chat-screen")

    async def test_api_key_saved_to_session_storage(
        self, page: Page, test_server: SimpleNamespace
    ) -> None:
        await page.goto(test_server.url)
        await page.fill("#api-key-input", test_server.api_key)
        await page.click("#auth-btn")
        await page.wait_for_selector("#chat-screen:not([hidden])", timeout=6000)

        saved = await page.evaluate("sessionStorage.getItem('leashd_key')")
        assert saved == test_server.api_key

    async def test_auto_reconnect_on_reload(
        self, page: Page, test_server: SimpleNamespace
    ) -> None:
        # Authenticate first
        await page.goto(test_server.url)
        await page.fill("#api-key-input", test_server.api_key)
        await page.click("#auth-btn")
        await page.wait_for_selector("#chat-screen:not([hidden])", timeout=6000)

        # Reload — key is in sessionStorage, should auto-connect
        await page.reload()
        await page.wait_for_selector("#chat-screen:not([hidden])", timeout=6000)
        await page.wait_for_selector(".dot-connected", timeout=6000)

    async def test_empty_key_prevented_by_validation(
        self, page: Page, test_server: SimpleNamespace
    ) -> None:
        await page.goto(test_server.url)
        # Don't fill anything — click submit directly
        initial_ws_count = len(test_server.chat_ids)
        await page.click("#auth-btn")
        # HTML5 required validation fires; no WS connection should be opened
        await asyncio.sleep(0.3)
        assert len(test_server.chat_ids) == initial_ws_count
        assert await page.is_visible("#auth-screen")
