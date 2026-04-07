# Plan: Dashboard UI Regression Testing with Playwright

## Status: COMPLETED ✓

All phases implemented and tests passing: **18 passed, 6 skipped** (skips = Kafka/NATS features disabled on broker).

To run:
```powershell
# From tests/ directory with broker running on port 4000
$env:DASHBOARD_USERNAME="Admin"
$env:DASHBOARD_PASSWORD="Admin"
c:\Projects\monster-mq\tests\.venv\Scripts\pytest c:\Projects\monster-mq\tests\ui\ -v
```

---


## TL;DR

Add Playwright-based UI regression tests for the MonsterMQ dashboard. **Recommended approach: Playwright with Python (pytest-playwright)** integrated into the existing `tests/` pytest infrastructure. This reuses the existing test framework, fixtures, and Python expertise while Playwright provides excellent Shadow DOM support needed for Siemens iX web components.

## Context & Decision Rationale

**Dashboard tech stack:** Vanilla JS + Siemens iX web components (Shadow DOM) + Vite + GraphQL client. No existing UI tests.

**Existing test infra:** Python pytest in `tests/` with markers, fixtures, HTML reports, parallel execution (xdist). Tests cover MQTT, GraphQL, REST, OPC UA, DB backends.

### Options Evaluated

| Framework | Shadow DOM | Language | Fits existing infra | Notes |
|-----------|-----------|----------|---------------------|-------|
| **Playwright (Python)** | Excellent (pierce selectors) | Python | pytest-playwright | **Recommended** — integrates with existing pytest suite |
| Playwright (JS/TS) | Excellent | JS/TS | Separate runner | Better native tooling but adds a second test stack |
| Cypress | Limited shadow DOM | JS | Separate runner | Shadow DOM support historically problematic for web components |
| Selenium | Manual shadow piercing | Python | pytest-compatible | Older, more boilerplate, slower |

**Key decision: Python over JS/TS** because:
1. Existing pytest infrastructure with fixtures, markers, reporters, xdist
2. Team already writes Python integration tests
3. Can share auth fixtures, GraphQL URL config, broker connection details
4. Single `pytest` command runs all tests (protocol + API + UI)
5. Playwright's Python API is feature-complete (codegen, trace viewer, screenshots)

**Shadow DOM is critical:** Siemens iX uses web components with Shadow DOM. Playwright's `>>` pierce selector and built-in shadow DOM traversal make it the best choice. Cypress requires workarounds.

## Implementation Phases

### Phase 1: Setup & Infrastructure ✓

1. ✓ **Added playwright dependencies** to `tests/requirements.txt` (`pytest-playwright>=0.6.2`, `playwright>=1.52.0`)
2. ✓ **Installed Playwright browsers** — Chromium (headless)
3. ✓ **Added pytest marker** `ui` and `ui/` to testpaths in `tests/pytest.ini`
4. ✓ **Created UI test infrastructure**: `tests/ui/__init__.py`, `tests/ui/conftest.py`, `tests/ui/ix_helpers.py`

### Phase 2: Core Test Helpers ✓

5. ✓ **`IxHelpers` class** in `tests/ui/ix_helpers.py`:
   - `click_menu_item(label)` — `ix-menu-item[label='...']` + networkidle
   - `fill_ix_input(selector, value)` — Shadow DOM pierce for iX inputs
   - `assert_toast_error(text)` / `assert_no_toast_error()` — targets `#error-toast` div
   - `assert_toast_success(text)` — targets `#success-toast` div
   - `get_table_row_texts(selector)`, `assert_table_contains_row()`

### Phase 3: Regression Tests ✓

6. ✓ **`tests/ui/test_login.py`** — login renders, invalid creds show error, redirect on success
7. ✓ **`tests/ui/test_dashboard.py`** — page title, sidebar renders, menu items, main content, navigation
8. ✓ **`tests/ui/test_mqtt_client_validation.py`** — 10 tests covering PUBLISH/SUBSCRIBE wildcard validation
9. ✓ **`tests/ui/test_bridge_validations.py`** — Kafka + NATS bridge validation (auto-skip if feature disabled)

### Phase 4: Extended Coverage (future)

10. **Navigation regression test** — all sidebar items load without errors
11. **Screenshot regression** — `expect(page).to_have_screenshot()` visual baseline


## Files Created/Modified

| File | Status | Purpose |
|------|--------|---------|
| `tests/requirements.txt` | ✓ Modified | Added pytest-playwright + playwright |
| `tests/pytest.ini` | ✓ Modified | Added `ui` marker and testpath |
| `tests/ui/__init__.py` | ✓ Created | Package marker |
| `tests/ui/conftest.py` | ✓ Created | Playwright fixtures with 3-step auth fallback |
| `tests/ui/ix_helpers.py` | ✓ Created | Siemens iX / Shadow DOM helpers |
| `tests/ui/test_login.py` | ✓ Created | Login flow regression (3 tests) |
| `tests/ui/test_dashboard.py` | ✓ Created | Dashboard smoke tests (5 tests) |
| `tests/ui/test_mqtt_client_validation.py` | ✓ Created | Wildcard validation regression (10 tests) |
| `tests/ui/test_bridge_validations.py` | ✓ Created | Bridge connector validation (6 tests, auto-skip) |

## Implementation Notes

### Auth Strategy
`authenticated_page` fixture uses a 3-step fallback:
1. Login with `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` env vars
2. Fall back to guest/anonymous mode (`#guest-link`) if `anonymousEnabled: true`
3. `pytest.skip()` if neither works

Tests requiring GraphQL mutations (e.g. `mqtt_test_client` fixture) additionally call `_get_auth_token()` directly via HTTP login if the browser has no JWT — then skip if still no token.

### Selector Patterns
- Modal trigger: `button[onclick='showAddAddressModal()']` (avoids strict-mode with duplicate text)
- Modal submit: `button[onclick='addAddress()']` (outside `<form>`, in `.modal-footer`)
- Toast errors: `#error-toast` div (NOT ix-toast web components)
- Menu items: `ix-menu-item[label='...']`

### Known Skips
- `TestKafkaValidation` — skips when `Kafka` feature disabled
- `test_nats_*` — skips when `Nats` feature disabled

## Verification

**Result: 18 passed, 6 skipped in 69.65s**

Running with credentials:
```powershell
$env:DASHBOARD_USERNAME="Admin"
$env:DASHBOARD_PASSWORD="Admin"
c:\Projects\monster-mq\tests\.venv\Scripts\pytest c:\Projects\monster-mq\tests\ui\ -v
```

Running without credentials (guest mode — login + dashboard tests only):
```powershell
c:\Projects\monster-mq\tests\.venv\Scripts\pytest c:\Projects\monster-mq\tests\ui\ -v
```

## Design Decisions

- **Python over JS/TS**: single test runner, reuses existing fixtures/markers/reporters
- **Chromium-only default**: fast; add browsers via `--browser` flag
- **pytest-playwright over raw Playwright**: provides `page`, `browser`, `context` fixtures out of the box
- **Separate `tests/ui/` directory**: isolated, skippable, no interference with protocol tests
- **iX helper class**: abstracts Shadow DOM complexity, keeps tests readable

## Considerations

1. **Broker must be running**: UI tests need a running broker on port 4000. A `broker_available` fixture should ping the URL and `pytest.skip()` if unavailable.
2. **Test data seeding**: Use GraphQL mutations in fixtures to create/cleanup test data (matches existing `tests/graphql/` patterns).
3. **Headed debugging**: pytest-playwright defaults to headless. Use `--headed` flag for local debugging.
