---
name: ui-test-developer
description: >
  Guide for writing Playwright UI regression tests for the MonsterMQ dashboard. Use this skill
  whenever the user wants to create, modify, or run browser-based UI tests. This covers
  dashboard page tests, form validation tests, navigation smoke tests, and Siemens iX web
  component interactions. Trigger on: "write UI test", "add Playwright test", "test the dashboard",
  "browser test", "regression test", "test form validation", "test navigation", "playwright",
  "Shadow DOM", "iX component", or any work on files in tests/ui/.
---

# MonsterMQ UI Test Development Skill

You are helping a developer write Playwright-based UI regression tests for the MonsterMQ
dashboard. Tests run against a live broker with dashboard on `http://localhost:4000`.

The dashboard uses **Siemens iX web components** (Shadow DOM), vanilla JS, and a Vite build.
Playwright Python (`pytest-playwright`) is integrated into the existing `tests/` pytest suite.

## Test Environment

### Setup
```bash
cd tests
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Running Tests
```bash
cd tests

# Run all UI tests
pytest ui/ -v

# Run only UI tests via marker
pytest -m ui -v

# Run a single test file
pytest ui/test_login.py -v

# Run headed (visible browser) for debugging
pytest ui/ --headed

# Run with trace recording (open with: playwright show-trace trace.zip)
pytest ui/ --tracing=on

# Skip UI tests when running protocol/API tests
pytest --ignore=tests/ui
pytest -m "not ui"
```

### Configuration
UI tests connect using environment variables:
```
DASHBOARD_URL=http://localhost:4000   (default)
MQTT_USERNAME=Test                    (default)
MQTT_PASSWORD=Test                    (default)
```

### pytest.ini
Located at `tests/pytest.ini`. Key settings include:
- Test paths include `ui/`
- Marker `ui` defined for all UI tests
- Chromium is the default browser

## Project Structure

```
tests/
  ui/
    __init__.py
    conftest.py               # Playwright fixtures (dashboard_url, authenticated_page, etc.)
    ix_helpers.py             # Siemens iX component interaction helpers
    test_login.py             # Login flow regression
    test_dashboard.py         # Dashboard smoke tests
    test_mqtt_client_validation.py   # MQTT bridge wildcard validation
    test_bridge_validations.py       # All bridge connector form validation
    test_navigation.py               # Sidebar navigation smoke test
```

## Shared Fixtures (tests/ui/conftest.py)

### `dashboard_url`
Returns the base URL of the dashboard (from env, default `http://localhost:4000`).

### `broker_available(dashboard_url)`
Pings the dashboard URL and calls `pytest.skip()` with a clear message if unreachable.
Always request this as a dependency in test files that access the live broker.

### `authenticated_page(page, dashboard_url, broker_available)`
Returns a Playwright `Page` already logged in. Navigates to the login page, fills credentials, waits for redirect to the main dashboard.

### `ix(page)` (alias for the ix_helpers module)
Returns an `IxHelpers` instance scoped to the given page — the recommended way to interact with Siemens iX components.

## Siemens iX Shadow DOM Patterns

Siemens iX uses Shadow DOM extensively. Standard `page.locator()` selectors **do not** pierce Shadow DOM by default.

### Playwright Pierce Selectors
Use `>>` to pierce into Shadow DOM:
```python
# Click a button inside a Shadow DOM host
page.locator("ix-button >> button").click()

# Fill an input inside ix-input
page.locator("ix-input#my-field >> input").fill("value")
```

### CSS :host Pattern (Prefer Accessible Names)
Prefer selectors tied to stable attributes over visual labels:
```python
# Prefer data-testid or name attributes when available
page.locator("[data-testid='submit-btn']").click()

# Or use aria roles
page.get_by_role("button", name="Save").click()
```

### ix-toast Assertions
The broker dashboard shows validation errors as `ix-toast` notifications:
```python
# Wait for a toast notification with specific text
toast = page.locator("ix-toast")
await expect(toast).to_be_visible(timeout=5000)
await expect(toast).to_contain_text("wildcard")
```

### ix-menu Navigation
The sidebar uses `ix-menu` with `ix-menu-item` elements:
```python
# Click a sidebar menu item
page.locator("ix-menu-item", has_text="MQTT Clients").click()
page.wait_for_url("**/pages/mqtt-clients.html", timeout=5000)
```

## IxHelpers Class (tests/ui/ix_helpers.py)

Use the `IxHelpers` class to abstract common iX interactions. Always prefer these
methods over raw Playwright calls in test files.

```python
ix = IxHelpers(page)

# Navigate sidebar
ix.click_menu_item("MQTT Clients")

# Form interactions
ix.fill_input("#local-topic", "sensor/+/temperature")
ix.select_dropdown("#mode-select", "PUBLISH")
ix.click_button("Save")

# Assertions
ix.assert_toast_error("wildcard")
ix.assert_toast_success("saved")
ix.dismiss_toast()

# Table helpers
rows = ix.get_table_rows("table.addresses")
assert any("sensor/temperature" in row for row in rows)
```

## Writing Tests

### Test File Template

```python
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui


def test_my_ui_feature(authenticated_page: Page, ix):
    """Test description of what this validates."""
    page = authenticated_page

    # Navigate to the relevant page
    ix.click_menu_item("Relevant Page")
    page.wait_for_load_state("networkidle")

    # Interact with the page
    ix.click_button("Add")
    ix.fill_input("#some-field", "test value")
    ix.click_button("Save")

    # Assert expected outcome
    ix.assert_toast_success("created")
```

### Form Validation Test Pattern

Used for testing that invalid input is rejected with an error message:

```python
def test_rejects_wildcard_topic(authenticated_page: Page, ix):
    """Wildcard characters must be rejected in publish destination topics."""
    page = authenticated_page

    # Navigate and open add form
    ix.click_menu_item("MQTT Clients")
    page.locator("ix-button", has_text="Add Client").click()

    # Fill the form with invalid data
    ix.fill_input("#remote-topic", "sensor/+/temperature")
    ix.select_dropdown("#mode", "PUBLISH")
    ix.click_button("Save")

    # Assert error shown, no success
    ix.assert_toast_error("wildcard")
    expect(page.locator("ix-toast.success")).not_to_be_visible()
```

### Negative + Positive Coverage Pattern

For validation tests, always cover both the rejection case AND the acceptance case:

```python
@pytest.mark.parametrize("topic,mode,should_fail", [
    ("sensor/+/temp", "PUBLISH", True),   # wildcard in publish destination
    ("sensor/#",      "PUBLISH", True),   # wildcard in publish destination
    ("sensor/+/temp", "SUBSCRIBE", False), # wildcard in subscribe source — OK
    ("sensor/temp",   "PUBLISH", False),  # clean topic — OK
])
def test_topic_validation(authenticated_page, ix, topic, mode, should_fail):
    ...
```

### Navigation Smoke Test Pattern

```python
@pytest.mark.parametrize("menu_label,expected_url_part", [
    ("Dashboard", "dashboard.html"),
    ("Sessions", "sessions.html"),
    ("MQTT Clients", "mqtt-clients.html"),
])
def test_navigation(authenticated_page: Page, ix, menu_label, expected_url_part):
    """All sidebar items navigate to the correct page without JS errors."""
    page = authenticated_page
    js_errors = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    ix.click_menu_item(menu_label)
    page.wait_for_url(f"**/{expected_url_part}", timeout=5000)
    assert js_errors == [], f"JS errors on {menu_label}: {js_errors}"
```

## Test Conventions

1. **Marker**: Apply `pytestmark = pytest.mark.ui` at module level for all files in `tests/ui/`
2. **File naming**: `test_<feature_area>.py` — one file per feature area
3. **Test naming**: `test_<specific_behavior>` — describe what is validated, not how
4. **Fixtures**: Always include `broker_available` (directly or via `authenticated_page`) so tests skip cleanly when broker is down
5. **Waits**: Use `wait_for_load_state("networkidle")` after navigation, not `time.sleep()`
6. **Assertions**: Use `expect()` from `playwright.sync_api` for element assertions — it retries automatically
7. **Screenshots on failure**: Playwright captures screenshots automatically on test failure with `--screenshot=only-on-failure`
8. **Parametrize validation tests**: Use `@pytest.mark.parametrize` for valid/invalid input combinations
9. **Isolation**: Each test should be self-contained; GraphQL mutations in fixtures should create and clean up test data

## Debugging Failing Tests

```bash
# Run with headed browser to watch
pytest ui/test_login.py --headed -v

# Record trace and open in trace viewer
pytest ui/test_login.py --tracing=on -v
playwright show-trace test-results/trace.zip

# Run with slow-motion for visibility
pytest ui/ --headed --slowmo=500
```

## Key Dashboard Files (for reference when writing tests)

- `dashboard/src/js/sidebar.js` — `SidebarManager.getMenuConfig()` — all menu items and their URLs
- `dashboard/src/js/graphql-client.js` — auth flow, JWT token handling
- `dashboard/src/pages/login.html` — login page structure and element IDs
- `dashboard/src/js/mqtt-client-detail.js` — MQTT bridge form: `addAddress()`, `updateAddress()`, `validatePublishTopic()`
- `dashboard/src/js/kafka-client-detail.js` — Kafka bridge form validation
- `dashboard/src/js/nats-client-detail.js` — NATS bridge form validation
- `dashboard/src/js/redis-client-detail.js` — Redis bridge form validation

## Dependencies

Key packages in `tests/requirements.txt` for UI testing:
```
pytest-playwright>=0.6.2     # pytest fixtures: page, browser, context
playwright>=1.52.0            # Browser automation, Shadow DOM support
```

Install browsers after pip install:
```bash
playwright install chromium
```
