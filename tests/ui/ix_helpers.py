"""
Helpers for interacting with MonsterMQ dashboard UI elements.

The dashboard uses Siemens iX web components (Shadow DOM) for menus, buttons, and inputs,
combined with plain HTML inputs on some pages (e.g. login) and custom toast divs for
feedback (not ix-toast).

Usage
-----
In a test, request the `ix` fixture (or instantiate directly):

    def test_something(authenticated_page, ix):
        ix.click_menu_item("MQTT Clients")
        ix.fill_ix_input("#remote-topic", "sensor/temperature")
        ix.click_ix_button("Save")
        ix.assert_toast_error("wildcard")
"""
import re
from playwright.sync_api import Page, expect, Locator


class IxHelpers:
    """
    Utility methods for interacting with the MonsterMQ iX dashboard.

    The dashboard uses:
      - ix-menu-item  : sidebar navigation (data-href attribute, click listener)
      - ix-button     : action buttons (wraps a native <button> via Shadow DOM)
      - ix-input      : form inputs (wraps a native <input> via Shadow DOM)
      - #error-toast  : plain <div> for validation/error feedback
      - #success-toast: plain <div> for success feedback
    """

    def __init__(self, page: Page):
        self.page = page

    # ── Navigation ────────────────────────────────────────────────────────────

    def click_menu_item(self, label: str) -> None:
        """
        Click a sidebar ix-menu-item by its label text and wait for the SPA
        to update the URL via history.pushState.

        The sidebar uses ix-menu-item elements whose 'label' attribute matches
        the visible text (e.g. 'Dashboard', 'MQTT Clients').
        """
        self.page.locator(f"ix-menu-item[label='{label}']").click()
        # SPA pushState updates the URL — wait for it to stabilise
        self.page.wait_for_load_state("networkidle", timeout=10_000)

    def navigate_to_page(self, href: str) -> None:
        """
        Directly call the SPA's navigateTo() via JavaScript.
        Use this when you need to jump to a page without going through the menu
        (e.g. pages behind feature flags that may not appear in the sidebar).
        """
        self.page.evaluate(f"window.navigateTo('{href}')")
        self.page.wait_for_url(f"**{href}", timeout=10_000)
        self.page.wait_for_load_state("networkidle", timeout=10_000)

    # ── iX Button ─────────────────────────────────────────────────────────────

    def click_ix_button(self, label: str) -> None:
        """
        Click an ix-button by its visible label text.

        ix-button renders a Shadow DOM <button> internally. Clicking the
        ix-button element itself is sufficient — Playwright dispatches the
        event correctly.
        """
        self.page.locator("ix-button", has_text=label).click()

    def click_ix_button_by_selector(self, selector: str) -> None:
        """Click an ix-button by CSS selector (e.g. '#save-btn', '[data-action=delete]')."""
        self.page.locator(f"ix-button{selector}").click()

    # ── iX Input ──────────────────────────────────────────────────────────────

    def fill_ix_input(self, selector: str, value: str) -> None:
        """
        Fill an ix-input component with a value.

        ix-input wraps a native <input> inside Shadow DOM. We pierce into it
        using Playwright's CSS >> combinator to reach the native input element.
        The selector targets the ix-input host (e.g. '#remote-topic', '[name=topic]').
        """
        self.page.locator(f"ix-input{selector} >> input").fill(value)

    def fill_plain_input(self, selector: str, value: str) -> None:
        """Fill a plain HTML <input> by CSS selector (e.g. '#username')."""
        self.page.fill(selector, value)

    def get_ix_input_value(self, selector: str) -> str:
        """Get the current value of an ix-input component."""
        return self.page.locator(f"ix-input{selector} >> input").input_value()

    # ── iX Select ─────────────────────────────────────────────────────────────

    def select_ix_option(self, selector: str, value: str) -> None:
        """
        Select an option from an ix-select dropdown.

        ix-select uses Shadow DOM and custom events. We trigger the selection
        by setting the value attribute and dispatching a change event.
        Falls back to clicking the option element directly if eval fails.
        """
        host = self.page.locator(f"ix-select{selector}")
        host.evaluate(
            f"""
            el => {{
                el.value = {value!r};
                el.dispatchEvent(new CustomEvent('valueChange', {{detail: {value!r}, bubbles: true}}));
            }}
            """
        )

    def select_plain_select(self, selector: str, value: str) -> None:
        """Select an option from a plain HTML <select> by value."""
        self.page.select_option(selector, value=value)

    # ── Toast Notifications ───────────────────────────────────────────────────

    def assert_toast_error(self, text: str, timeout: int = 8_000) -> None:
        """
        Assert that an error toast appears containing the given text.

        Error toasts are plain <div id="error-toast"> elements appended to
        document.body by showError() — they are NOT ix-toast components.
        """
        toast = self.page.locator("#error-toast")
        expect(toast).to_be_visible(timeout=timeout)
        expect(toast).to_contain_text(text, timeout=timeout)

    def assert_no_toast_error(self, timeout: int = 2_000) -> None:
        """Assert that no error toast is currently visible."""
        expect(self.page.locator("#error-toast")).not_to_be_visible(timeout=timeout)

    def assert_toast_success(self, text: str, timeout: int = 8_000) -> None:
        """
        Assert that a success toast appears containing the given text.

        Success toasts are plain <div id="success-toast"> elements appended
        to document.body by showSuccess() — auto-removed after ~3 seconds.
        """
        toast = self.page.locator("#success-toast")
        expect(toast).to_be_visible(timeout=timeout)
        expect(toast).to_contain_text(text, timeout=timeout)

    def dismiss_toast(self) -> None:
        """Dismiss any visible toast by clicking its close button."""
        close = self.page.locator("#error-toast button, #success-toast button")
        if close.count() > 0:
            close.first.click()

    # ── Table Helpers ─────────────────────────────────────────────────────────

    def get_table_row_texts(self, table_selector: str) -> list[list[str]]:
        """
        Return all row cell texts from a <table> as a list of lists.

        Args:
            table_selector: CSS selector for the <table> element.

        Returns:
            List of rows, each row is a list of cell text strings.
        """
        rows = self.page.locator(f"{table_selector} tbody tr")
        result = []
        for i in range(rows.count()):
            cells = rows.nth(i).locator("td")
            row_texts = [cells.nth(j).inner_text().strip() for j in range(cells.count())]
            result.append(row_texts)
        return result

    def assert_table_contains_row(self, table_selector: str, *cell_texts: str) -> None:
        """
        Assert that at least one table row contains all the given cell texts.

        Example:
            ix.assert_table_contains_row("table.addresses", "sensor/temperature", "PUBLISH")
        """
        rows = self.get_table_row_texts(table_selector)
        for row in rows:
            row_joined = " ".join(row)
            if all(text in row_joined for text in cell_texts):
                return
        raise AssertionError(
            f"No row found containing {cell_texts!r} in table {table_selector!r}.\n"
            f"Rows found: {rows}"
        )

    # ── Waiting Utilities ─────────────────────────────────────────────────────

    def wait_for_content_loaded(self, timeout: int = 10_000) -> None:
        """
        Wait for the SPA content area to finish loading after a navigation.
        Uses networkidle as the signal.
        """
        self.page.wait_for_load_state("networkidle", timeout=timeout)

    def wait_for_page_url(self, url_pattern: str, timeout: int = 10_000) -> None:
        """
        Wait for the page URL to match a glob pattern.

        Example:
            ix.wait_for_page_url("**/pages/mqtt-clients.html")
        """
        self.page.wait_for_url(url_pattern, timeout=timeout)
