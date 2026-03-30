"""E2E tests for the command palette (slash command autocomplete)."""

import asyncio

import pytest
from playwright.async_api import Page

from tests.e2e.conftest import SimpleNamespace


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.e2e
class TestCommandPalette:
    async def test_slash_shows_palette(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "/")
        await authed_page.dispatch_event("#message-input", "input")
        await authed_page.wait_for_selector(
            "#command-palette:not([hidden])", timeout=5000
        )
        items = authed_page.locator("#command-palette .command-palette-item")
        assert await items.count() > 0

    async def test_palette_filters_on_prefix(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "/ta")
        await authed_page.dispatch_event("#message-input", "input")
        await authed_page.wait_for_selector(
            "#command-palette:not([hidden])", timeout=5000
        )
        items = authed_page.locator("#command-palette .command-palette-item")
        count = await items.count()
        assert count >= 2  # at least /task and /tasks

        names = []
        for i in range(count):
            text = await items.nth(i).text_content()
            names.append(text)
        combined = " ".join(names)
        assert "/task" in combined
        assert "/tasks" in combined

    async def test_palette_hides_after_space(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "/task ")
        await authed_page.dispatch_event("#message-input", "input")
        await asyncio.sleep(0.2)
        is_hidden = await authed_page.evaluate(
            "document.querySelector('#command-palette')?.hidden ?? true"
        )
        assert is_hidden

    async def test_palette_arrow_keys(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        # Use click + type to simulate real keyboard input (fill doesn't fire keydown)
        await authed_page.click("#message-input")
        await authed_page.type("#message-input", "/")
        await authed_page.wait_for_selector(
            "#command-palette:not([hidden])", timeout=5000
        )

        # First item should be active by default
        first_item = authed_page.locator("#command-palette .command-palette-item").first
        assert "active" in (await first_item.get_attribute("class") or "")

        # Press ArrowDown to move selection
        await authed_page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.2)
        second_item = authed_page.locator("#command-palette .command-palette-item").nth(
            1
        )
        assert "active" in (await second_item.get_attribute("class") or "")

    async def test_palette_tab_selects(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "/pl")
        await authed_page.dispatch_event("#message-input", "input")
        await authed_page.wait_for_selector(
            "#command-palette:not([hidden])", timeout=5000
        )

        await authed_page.press("#message-input", "Tab")
        await asyncio.sleep(0.2)

        value = await authed_page.input_value("#message-input")
        assert value == "/plan "

    async def test_palette_escape_hides(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "/")
        await authed_page.dispatch_event("#message-input", "input")
        await authed_page.wait_for_selector(
            "#command-palette:not([hidden])", timeout=5000
        )

        await authed_page.press("#message-input", "Escape")
        await asyncio.sleep(0.2)
        is_hidden = await authed_page.evaluate(
            "document.querySelector('#command-palette')?.hidden ?? true"
        )
        assert is_hidden

        # Input should still contain "/"
        value = await authed_page.input_value("#message-input")
        assert value == "/"

    async def test_palette_no_match_hides(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "/zzz")
        await authed_page.dispatch_event("#message-input", "input")
        await asyncio.sleep(0.2)
        is_hidden = await authed_page.evaluate(
            "document.querySelector('#command-palette')?.hidden ?? true"
        )
        assert is_hidden
