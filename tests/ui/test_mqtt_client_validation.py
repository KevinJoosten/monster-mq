"""
UI regression tests for MQTT Client bridge address wildcard validation.

Tests the PR fix: MQTT publish destinations must not contain wildcard characters.

Wildcard rules (MQTT spec [MQTT-3.3.2-2]):
  SUBSCRIBE mode (Remote → Local):
    - Remote Topic: subscription source — wildcards ALLOWED
    - Local Topic:  publish destination — wildcards FORBIDDEN
  PUBLISH mode (Local → Remote):
    - Local Topic:  subscription source — wildcards ALLOWED
    - Remote Topic: publish destination — wildcards FORBIDDEN

Setup:
  Creates a temporary MQTT client via GraphQL before tests, deletes it after.
  Requires the MqttClient feature to be enabled on the broker.
"""
import os
import uuid
import pytest
import requests
from playwright.sync_api import Page, expect
from ui.ix_helpers import IxHelpers

pytestmark = pytest.mark.ui

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:4000")
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME") or os.getenv("MQTT_USERNAME", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD") or os.getenv("MQTT_PASSWORD", "")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _graphql(dashboard_url: str, query: str, variables: dict = None, token: str = None) -> dict:
    """Execute a GraphQL query/mutation against the broker."""
    headers = {"Content-Type": "application/json"}
    if token and token != "null":
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.post(
        f"{dashboard_url}/graphql",
        json={"query": query, "variables": variables or {}},
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data and data["errors"]:
        raise AssertionError(f"GraphQL errors: {data['errors']}")
    return data.get("data", {})


def _get_token(page: Page) -> str | None:
    """Read the auth token from the browser's localStorage."""
    token = page.evaluate("() => localStorage.getItem('monstermq_token')")
    return token if (token and token != "null") else None


def _get_auth_token(dashboard_url: str, username: str, password: str) -> str | None:
    """Obtain a JWT token via the GraphQL login mutation."""
    if not username:
        return None
    try:
        resp = requests.post(
            f"{dashboard_url}/graphql",
            json={
                "query": "mutation($u:String!,$p:String!){login(username:$u,password:$p){success token}}",
                "variables": {"u": username, "p": password},
            },
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        login = data.get("data", {}).get("login", {})
        return login.get("token") if login.get("success") else None
    except Exception:
        return None


def _mqtt_client_feature_enabled(dashboard_url: str) -> bool:
    """Return True if the MqttClient feature is enabled on this broker."""
    try:
        data = _graphql(dashboard_url, "{ broker { enabledFeatures } }")
        features = data.get("broker", {}).get("enabledFeatures", [])
        return "MqttClient" in features
    except Exception:
        return False


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mqtt_test_client(authenticated_page: Page):
    """
    Create a temporary MQTT client bridge via GraphQL for testing.
    Yields the client name, then deletes the client after the test.
    """
    if not _mqtt_client_feature_enabled(DASHBOARD_URL):
        pytest.skip("MqttClient feature is not enabled on this broker")

    token = _get_token(authenticated_page) or _get_auth_token(
        DASHBOARD_URL, DASHBOARD_USERNAME, DASHBOARD_PASSWORD
    )
    if not token:
        pytest.skip(
            "No auth token available for GraphQL mutations "
            "(set DASHBOARD_USERNAME / DASHBOARD_PASSWORD env vars)"
        )
    name = f"ui-test-{uuid.uuid4().hex[:8]}"

    create_mutation = """
        mutation CreateMqttClient($input: MqttClientInput!) {
            mqttClient {
                create(input: $input) { success errors }
            }
        }
    """
    result = _graphql(DASHBOARD_URL, create_mutation, {
        "input": {
            "name": name,
            "namespace": "ui-test",
            "nodeId": "*",
            "enabled": False,
            "config": {
                "brokerUrl": "tcp://localhost:1883",
                "clientId": f"ui-test-{name}",
            }
        }
    }, token=token)

    success = result.get("mqttClient", {}).get("create", {}).get("success", False)
    errors = result.get("mqttClient", {}).get("create", {}).get("errors", [])
    if not success:
        pytest.skip(f"Could not create test MQTT client: {errors}")

    yield name

    # Cleanup — best effort
    try:
        _graphql(
            DASHBOARD_URL,
            "mutation DeleteMqttClient($name: String!) { mqttClient { delete(name: $name) } }",
            {"name": name},
            token=token,
        )
    except Exception:
        pass


@pytest.fixture
def mqtt_detail_page(authenticated_page: Page, mqtt_test_client: str) -> tuple[Page, IxHelpers]:
    """
    Navigate to the MQTT client detail page for the test client.
    Returns (page, IxHelpers) tuple ready for interaction.
    """
    page = authenticated_page
    ix = IxHelpers(page)

    # Navigate directly — the page uses ?client=<name> query param
    page.goto(f"{DASHBOARD_URL}/pages/mqtt-client-detail.html?client={mqtt_test_client}")
    page.wait_for_load_state("networkidle", timeout=15_000)

    # Confirm the page loaded the correct client (subtitle shows namespace or client name)
    expect(page.locator("#page-subtitle")).to_be_visible(timeout=8_000)

    return page, ix


# ── Tests: PUBLISH mode ───────────────────────────────────────────────────────

def test_publish_mode_remote_topic_rejects_plus_wildcard(mqtt_detail_page):
    """PUBLISH mode: remote topic (publish destination) must reject '+' wildcard."""
    page, ix = mqtt_detail_page

    # Open the add-address modal
    page.locator("button[onclick='showAddAddressModal()']").click()
    expect(page.locator("#add-address-modal")).to_be_visible(timeout=5_000)

    # Set PUBLISH mode and enter an invalid remote topic
    page.select_option("#address-mode", "PUBLISH")
    page.fill("#address-remote-topic", "sensor/+/temperature")
    page.fill("#address-local-topic", "local/sensor/temperature")

    page.locator("button[onclick='addAddress()']").click()

    ix.assert_toast_error("wildcard")
    # Modal should stay open (validation failed)
    expect(page.locator("#add-address-modal")).to_be_visible()


def test_publish_mode_remote_topic_rejects_hash_wildcard(mqtt_detail_page):
    """PUBLISH mode: remote topic (publish destination) must reject '#' wildcard."""
    page, ix = mqtt_detail_page

    page.locator("button[onclick='showAddAddressModal()']").click()
    expect(page.locator("#add-address-modal")).to_be_visible(timeout=5_000)

    page.select_option("#address-mode", "PUBLISH")
    page.fill("#address-remote-topic", "sensor/#")
    page.fill("#address-local-topic", "local/sensor/temperature")

    page.locator("button[onclick='addAddress()']").click()

    ix.assert_toast_error("wildcard")


def test_publish_mode_local_topic_allows_wildcards(mqtt_detail_page):
    """PUBLISH mode: local topic is the subscription source — wildcards are ALLOWED."""
    page, ix = mqtt_detail_page

    page.locator("button[onclick='showAddAddressModal()']").click()
    expect(page.locator("#add-address-modal")).to_be_visible(timeout=5_000)

    page.select_option("#address-mode", "PUBLISH")
    page.fill("#address-remote-topic", "sensor/temperature")   # clean destination
    page.fill("#address-local-topic", "local/sensor/+/temperature")  # wildcards OK in source

    page.locator("button[onclick='addAddress()']").click()

    # Must NOT show a wildcard error
    ix.assert_no_toast_error()


# ── Tests: SUBSCRIBE mode ────────────────────────────────────────────────────

def test_subscribe_mode_local_topic_rejects_plus_wildcard(mqtt_detail_page):
    """SUBSCRIBE mode: local topic (publish destination) must reject '+' wildcard."""
    page, ix = mqtt_detail_page

    page.locator("button[onclick='showAddAddressModal()']").click()
    expect(page.locator("#add-address-modal")).to_be_visible(timeout=5_000)

    page.select_option("#address-mode", "SUBSCRIBE")
    page.fill("#address-remote-topic", "sensor/+/temperature")   # wildcards OK in subscription
    page.fill("#address-local-topic", "remote/sensor/+/temperature")  # invalid destination

    page.locator("button[onclick='addAddress()']").click()

    ix.assert_toast_error("wildcard")


def test_subscribe_mode_local_topic_rejects_hash_wildcard(mqtt_detail_page):
    """SUBSCRIBE mode: local topic (publish destination) must reject '#' wildcard."""
    page, ix = mqtt_detail_page

    page.locator("button[onclick='showAddAddressModal()']").click()
    expect(page.locator("#add-address-modal")).to_be_visible(timeout=5_000)

    page.select_option("#address-mode", "SUBSCRIBE")
    page.fill("#address-remote-topic", "sensor/#")
    page.fill("#address-local-topic", "remote/sensor/#")  # invalid destination

    page.locator("button[onclick='addAddress()']").click()

    ix.assert_toast_error("wildcard")


def test_subscribe_mode_remote_topic_allows_wildcards(mqtt_detail_page):
    """SUBSCRIBE mode: remote topic is the subscription source — wildcards are ALLOWED."""
    page, ix = mqtt_detail_page

    page.locator("button[onclick='showAddAddressModal()']").click()
    expect(page.locator("#add-address-modal")).to_be_visible(timeout=5_000)

    page.select_option("#address-mode", "SUBSCRIBE")
    page.fill("#address-remote-topic", "sensor/+/temperature")  # wildcards OK in subscription
    page.fill("#address-local-topic", "remote/sensor/temperature")  # clean destination

    page.locator("button[onclick='addAddress()']").click()

    # Must NOT show a wildcard error (may show other errors like success, that's fine)
    ix.assert_no_toast_error()


# ── Tests: Valid topics ───────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,remote,local", [
    ("PUBLISH",   "sensor/temperature",     "local/sensor/temperature"),
    ("PUBLISH",   "plant/line1/machine1",   "local/sensor/+/temperature"),  # wildcard in source
    ("SUBSCRIBE", "sensor/+/temperature",   "remote/sensor/temperature"),   # wildcard in source
    ("SUBSCRIBE", "sensor/#",               "remote/data"),                  # wildcard in source
])
def test_valid_topics_accepted(mqtt_detail_page, mode: str, remote: str, local: str):
    """Valid topic combinations (no wildcards in publish destinations) must not show wildcard error."""
    page, ix = mqtt_detail_page

    page.locator("button[onclick='showAddAddressModal()']").click()
    expect(page.locator("#add-address-modal")).to_be_visible(timeout=5_000)

    page.select_option("#address-mode", mode)
    page.fill("#address-remote-topic", remote)
    page.fill("#address-local-topic", local)

    page.locator("button[onclick='addAddress()']").click()

    ix.assert_no_toast_error()

    # Wait for the modal to close (it auto-closes on successful submit)
    expect(page.locator("#add-address-modal")).to_be_hidden(timeout=5_000)
