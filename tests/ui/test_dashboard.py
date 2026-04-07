"""
Dashboard smoke tests — verify the main dashboard page loads and key UI elements render.

These tests do not require specific broker data; they verify the shell:
  - Page title visible
  - Sidebar (ix-menu) rendered with expected categories
  - Main content area has meaningful content (metric grid)
"""
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui

# Sidebar categories expected in a default MonsterMQ deployment
EXPECTED_CATEGORIES = ["Monitoring", "Configuration", "Bridging", "System"]


def test_dashboard_page_title(authenticated_page: Page):
    """Dashboard should show the 'Dashboard' heading in the main content area."""
    page = authenticated_page
    page.wait_for_url("**/pages/dashboard.html", timeout=10_000)
    expect(page.locator("h1.page-title")).to_contain_text("Dashboard")


def test_dashboard_sidebar_renders(authenticated_page: Page):
    """The ix-menu sidebar should render and contain the expected categories."""
    page = authenticated_page
    page.wait_for_url("**/pages/dashboard.html", timeout=10_000)

    ix_menu = page.locator("ix-menu")
    expect(ix_menu).to_be_visible(timeout=8_000)

    for category in EXPECTED_CATEGORIES:
        cat = page.locator(f"ix-menu-category[label='{category}']")
        expect(cat).to_be_attached(timeout=8_000), f"Sidebar category '{category}' not found"


def test_dashboard_monitoring_menu_item(authenticated_page: Page):
    """The 'Dashboard' menu item in Monitoring should be present in the sidebar."""
    page = authenticated_page
    page.wait_for_url("**/pages/dashboard.html", timeout=10_000)

    dashboard_item = page.locator("ix-menu-item[label='Dashboard']")
    expect(dashboard_item).to_be_attached(timeout=8_000)


def test_dashboard_main_content_loads(authenticated_page: Page):
    """The main content area should be visible and not empty after load."""
    page = authenticated_page
    page.wait_for_url("**/pages/dashboard.html", timeout=10_000)

    main = page.locator("#main-content")
    expect(main).to_be_visible(timeout=8_000)
    # Cluster overview grid is populated by JS — wait for it to appear
    expect(page.locator("#cluster-overview")).to_be_visible(timeout=8_000)


def test_navigate_to_sessions_page(authenticated_page: Page):
    """Clicking the Sessions menu item should navigate to sessions.html."""
    page = authenticated_page
    page.wait_for_url("**/pages/dashboard.html", timeout=10_000)

    page.locator("ix-menu-item[label='Sessions']").click()
    page.wait_for_url("**/pages/sessions.html", timeout=8_000)
    # Page headline should reflect the new page
    expect(page.locator("h1.page-title")).to_be_visible(timeout=5_000)
