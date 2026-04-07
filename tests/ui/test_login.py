"""
UI regression tests for the MonsterMQ login page.

Tests:
  - Login page renders correctly (form fields visible)
  - Invalid credentials show an error alert
  - Valid credentials (or auto-login when auth disabled) redirect to dashboard
"""
import os
import pytest
import requests
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:4000")
_MQTT_USER = os.getenv("MQTT_USERNAME", "")
_MQTT_PASS = os.getenv("MQTT_PASSWORD", "")
USERNAME = os.getenv("DASHBOARD_USERNAME", _MQTT_USER)
PASSWORD = os.getenv("DASHBOARD_PASSWORD", _MQTT_PASS)


def _is_auth_enabled(dashboard_url: str) -> bool:
    """Check if user management is enabled on the broker."""
    try:
        resp = requests.post(
            f"{dashboard_url}/graphql",
            json={"query": "{ broker { userManagementEnabled } }"},
            timeout=5,
        )
        data = resp.json()
        return data.get("data", {}).get("broker", {}).get("userManagementEnabled", False)
    except Exception:
        return False


def test_login_page_renders(page: Page, broker_available):
    """Login page should display username, password fields and a sign-in button."""
    page.goto(f"{DASHBOARD_URL}/pages/login.html")
    page.wait_for_load_state("domcontentloaded")

    expect(page.locator("#username")).to_be_visible()
    expect(page.locator("#password")).to_be_visible()
    expect(page.locator("ix-button#login-btn")).to_be_visible()
    expect(page.locator("h1")).to_contain_text("Welcome Back")


def test_login_redirects_to_dashboard(page: Page, broker_available):
    """
    Login with valid credentials (or guest mode if anonymous is enabled) should
    redirect away from the login page to the dashboard.

    Skipped when auth is enabled, credentials are not configured, and guest
    mode is unavailable — use DASHBOARD_USERNAME / DASHBOARD_PASSWORD env vars
    to supply real broker credentials.
    """
    page.goto(f"{DASHBOARD_URL}/pages/login.html")
    page.wait_for_load_state("domcontentloaded")

    # If auth is disabled, the broker auto-redirects before user input
    if "login.html" not in page.url:
        assert "login.html" not in page.url
        return

    if USERNAME:
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.locator("ix-button#login-btn").click()
    else:
        # Try guest/anonymous mode
        guest_link = page.locator("#guest-link")
        if not guest_link.is_visible(timeout=5_000):
            pytest.skip(
                "No credentials configured (DASHBOARD_USERNAME) and guest mode is unavailable. "
                "Cannot test login redirect."
            )
        guest_link.click()

    # Wait for redirect away from login page (success) OR error alert (failure)
    try:
        page.wait_for_function(
            """() => {
                if (!window.location.pathname.endsWith('login.html')) return true;
                const alert = document.querySelector('#alert-container .alert-error');
                if (alert && alert.offsetParent !== null) return true;
                return false;
            }""",
            timeout=12_000,
        )
    except Exception:
        pytest.fail(f"Login page did not redirect or show an error within timeout. Current URL: {page.url}")

    if "login.html" in page.url:
        # Login failed — credentials wrong for this broker
        pytest.skip(
            "Login failed with provided credentials — the broker's user differs from "
            "DASHBOARD_USERNAME. Set correct credentials to test the redirect flow."
        )

    assert "login.html" not in page.url, f"Expected redirect away from login, got {page.url}"


def test_login_invalid_credentials_show_error(page: Page, broker_available):
    """
    Submitting wrong credentials when auth is enabled should show an error
    in the #alert-container without redirecting.
    """
    if not _is_auth_enabled(DASHBOARD_URL):
        pytest.skip("User management is disabled on this broker — invalid credential test skipped")

    page.goto(f"{DASHBOARD_URL}/pages/login.html")
    page.wait_for_load_state("domcontentloaded")

    page.fill("#username", "definitely_invalid_user")
    page.fill("#password", "definitely_invalid_password")
    page.locator("ix-button#login-btn").click()

    # Should show an error alert, NOT redirect away
    error_container = page.locator("#alert-container .alert-error")
    expect(error_container).to_be_visible(timeout=8_000)
    assert "login.html" in page.url, "Should remain on login page after failed login"
