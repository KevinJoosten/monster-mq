"""
Shared Playwright fixtures for MonsterMQ dashboard UI tests.

Prerequisites:
  - MonsterMQ broker running with dashboard on DASHBOARD_URL (default http://localhost:4000)
  - Playwright browsers installed: playwright install chromium

Running:
  pytest ui/ -v                  # all UI tests
  pytest -m ui                   # via marker
  pytest ui/ --headed            # visible browser for debugging
  pytest ui/ --tracing=on        # record traces (open with: playwright show-trace trace.zip)

Environment variables:
  DASHBOARD_URL       Base URL of the dashboard (default: http://localhost:4000)
  DASHBOARD_USERNAME  Dashboard login username (default: falls back to MQTT_USERNAME then empty)
  DASHBOARD_PASSWORD  Dashboard login password (default: falls back to MQTT_PASSWORD then empty)
  MQTT_USERNAME       MQTT protocol username (default: Test)
  MQTT_PASSWORD       MQTT protocol password (default: Test)

Fallback login strategy:
  1. Try credentials from DASHBOARD_USERNAME / DASHBOARD_PASSWORD
  2. If login fails (error alert appears), use anonymous/guest mode
  3. If even guest mode is unavailable, the fixture raises pytest.skip()
"""
import os
import pytest
import requests
from playwright.sync_api import Page
from ui.ix_helpers import IxHelpers

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:4000")
# Dashboard credentials — separate from MQTT protocol credentials
# Falls back to MQTT_USERNAME/MQTT_PASSWORD for convenience, then to empty strings
_MQTT_USER = os.getenv("MQTT_USERNAME", "")
_MQTT_PASS = os.getenv("MQTT_PASSWORD", "")
USERNAME = os.getenv("DASHBOARD_USERNAME", _MQTT_USER)
PASSWORD = os.getenv("DASHBOARD_PASSWORD", _MQTT_PASS)


@pytest.fixture(scope="session")
def dashboard_url() -> str:
    """Base URL for the MonsterMQ dashboard."""
    return DASHBOARD_URL


@pytest.fixture(scope="session")
def broker_available(dashboard_url: str):
    """
    Session-scoped fixture that pings the dashboard URL and skips the entire
    test session with a clear message if the broker is unreachable.

    Include this fixture (directly or via authenticated_page) in every UI test
    so tests skip cleanly when the broker is not running rather than failing
    with a cryptic connection error.
    """
    try:
        resp = requests.get(dashboard_url, timeout=5)
        resp.raise_for_status()
    except Exception as exc:
        pytest.skip(
            f"MonsterMQ dashboard not reachable at {dashboard_url} — "
            f"start the broker before running UI tests. ({exc})"
        )


def _try_guest_mode(page: Page, dashboard_url: str) -> bool:
    """
    Attempt to enter the dashboard via anonymous/guest mode.
    Returns True if successful, False if guest mode is not available.
    """
    try:
        resp = requests.post(
            f"{dashboard_url}/graphql",
            json={"query": "{ broker { anonymousEnabled } }"},
            timeout=5,
        )
        anonymous_enabled = resp.json().get("data", {}).get("broker", {}).get("anonymousEnabled", False)
    except Exception:
        anonymous_enabled = False

    if not anonymous_enabled:
        return False

    # Click "View dashboard (read-only)" guest link
    guest_link = page.locator("#guest-link")
    if guest_link.is_visible(timeout=5_000):
        guest_link.click()
        page.wait_for_url(f"{dashboard_url}/pages/dashboard.html", timeout=10_000)
        page.wait_for_load_state("networkidle", timeout=15_000)
        return True
    return False


@pytest.fixture
def authenticated_page(page: Page, dashboard_url: str, broker_available) -> Page:
    """
    Returns a Playwright Page already logged into (or with guest access to) the dashboard.

    Login strategy:
    1. Navigate to login page and submit credentials from DASHBOARD_USERNAME / DASHBOARD_PASSWORD.
    2. If credentials are invalid (error alert appears), fall back to guest/anonymous mode.
    3. If neither works, the test is skipped with a clear message.

    Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD env vars with the broker admin credentials
    to get full (non-guest) access for tests that mutate data via GraphQL.
    """
    page.goto(f"{dashboard_url}/pages/login.html")
    page.wait_for_load_state("domcontentloaded")

    # When auth is disabled the broker auto-redirects from login.html immediately
    # (checkUserManagementEnabled → autoLoginDisabled is called on page load)
    if "login.html" not in page.url:
        page.wait_for_load_state("networkidle", timeout=15_000)
        return page

    # Fill credentials using plain <input> fields (login page does NOT use iX inputs)
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.locator("ix-button#login-btn").click()

    # Wait for EITHER a redirect (success) or an error alert (wrong credentials)
    try:
        page.wait_for_function(
            """() => {
                // Redirect happened
                if (!window.location.pathname.endsWith('login.html')) return true;
                // Error alert appeared
                const alert = document.querySelector('#alert-container .alert-error');
                if (alert && alert.offsetParent !== null) return true;
                return false;
            }""",
            timeout=12_000,
        )
    except Exception:
        pass

    # Check if we successfully left the login page
    if "login.html" not in page.url:
        page.wait_for_load_state("networkidle", timeout=15_000)
        return page

    # Credentials failed — try guest/anonymous mode
    if _try_guest_mode(page, dashboard_url):
        return page

    # Nothing worked
    pytest.skip(
        f"Could not log into dashboard at {dashboard_url}. "
        "Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD env vars with valid broker credentials, "
        "or enable anonymous access on the broker."
    )


@pytest.fixture
def ix(page: Page) -> IxHelpers:
    """
    Returns an IxHelpers instance bound to the current page.
    Use this fixture in tests to interact with iX components and toasts.

    Note: tests that use `authenticated_page` should pass that page to helpers:
        def test_foo(authenticated_page, ix):
            # ix is bound to the same underlying Page object
    """
    return IxHelpers(page)
