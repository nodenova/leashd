"""E2E tests for the WebUI settings page."""

import pytest
from playwright.async_api import Page

from tests.e2e.conftest import SimpleNamespace


@pytest.mark.e2e
class TestSettingsPage:
    async def test_settings_button_opens_settings(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.click("#settings-btn")
        await authed_page.wait_for_selector(
            "#settings-screen:not([hidden])", timeout=4000
        )
        assert await authed_page.is_hidden("#chat-screen")

    async def test_back_button_returns_to_chat(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.click("#settings-btn")
        await authed_page.wait_for_selector(
            "#settings-screen:not([hidden])", timeout=4000
        )
        await authed_page.click("#settings-back-btn")
        await authed_page.wait_for_selector("#chat-screen:not([hidden])", timeout=4000)
        assert await authed_page.is_hidden("#settings-screen")

    async def test_theme_toggle_cycles_through_modes(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        # Default is "auto"
        initial = await authed_page.evaluate(
            "document.documentElement.getAttribute('data-theme')"
        )
        assert initial == "auto"

        await authed_page.click("#theme-toggle-btn")
        after_first = await authed_page.evaluate(
            "document.documentElement.getAttribute('data-theme')"
        )
        assert after_first == "dark"

        await authed_page.click("#theme-toggle-btn")
        after_second = await authed_page.evaluate(
            "document.documentElement.getAttribute('data-theme')"
        )
        assert after_second == "light"

    async def test_color_theme_swatch_applies_and_persists(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.click("#settings-btn")
        await authed_page.wait_for_selector(
            "#settings-screen:not([hidden])", timeout=4000
        )
        # Wait for settings body to be populated (it fetches /api/config)
        await authed_page.wait_for_selector(
            "#settings-body [data-color-theme]", timeout=6000
        )

        # Click a known color theme swatch
        await authed_page.click('[data-color-theme="dracula"]')

        color_theme = await authed_page.evaluate(
            "document.documentElement.getAttribute('data-color-theme')"
        )
        assert color_theme == "dracula"

        persisted = await authed_page.evaluate(
            "localStorage.getItem('leashd_color_theme')"
        )
        assert persisted == "dracula"
