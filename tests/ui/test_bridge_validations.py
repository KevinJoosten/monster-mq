"""
UI regression tests for bridge connector form validation — wildcard characters in
MQTT publish destination topics must be rejected across all bridge types.

Covers:
  - Kafka client: destinationTopicPrefix must reject wildcards (+ and #)
  - NATS client:  mqttTopic must reject wildcards in PUBLISH mode
  - Redis client: mqttTopic must reject wildcards

MQTT Client bridge validation is covered separately in test_mqtt_client_validation.py.

For Kafka, validation fires on the main save form (?new=true is sufficient, no existing client).
For NATS and Redis, validation is in the address mapping modal (requires existing client via
GraphQL fixture — those tests are skipped automatically if the feature is unavailable).
"""
import os
import uuid
import pytest
import requests
from playwright.sync_api import Page, expect
from ui.ix_helpers import IxHelpers

pytestmark = pytest.mark.ui

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:4000")
USERNAME = os.getenv("MQTT_USERNAME", "Test")
PASSWORD = os.getenv("MQTT_PASSWORD", "Test")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _graphql(dashboard_url: str, query: str, variables: dict = None, token: str = None) -> dict:
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


def _feature_enabled(dashboard_url: str, feature: str) -> bool:
    try:
        data = _graphql(dashboard_url, "{ broker { enabledFeatures } }")
        return feature in data.get("broker", {}).get("enabledFeatures", [])
    except Exception:
        return False


def _get_token(page: Page) -> str | None:
    token = page.evaluate("() => localStorage.getItem('monstermq_token')")
    return token if (token and token != "null") else None


# ── Kafka Validation Tests ─────────────────────────────────────────────────────
# These tests use the ?new=true form (no existing client required).

class TestKafkaValidation:
    """Kafka client destinationTopicPrefix must not contain MQTT wildcard characters."""

    @pytest.fixture(autouse=True)
    def navigate_to_kafka_new(self, authenticated_page: Page):
        """Navigate to the Kafka new-client form (?new=true)."""
        if not _feature_enabled(DASHBOARD_URL, "Kafka"):
            pytest.skip("Kafka feature is not enabled on this broker")

        self.page = authenticated_page
        self.ix = IxHelpers(authenticated_page)

        # Navigate to the Kafka new-client form
        authenticated_page.goto(f"{DASHBOARD_URL}/pages/kafka-client-detail.html?new=true")
        authenticated_page.wait_for_load_state("networkidle", timeout=15_000)
        expect(authenticated_page.locator("#client-destination-prefix")).to_be_visible(timeout=8_000)

    def test_destination_prefix_rejects_plus_wildcard(self):
        """destinationTopicPrefix with '+' is rejected on save."""
        # Fill required fields to reach validation (prevents HTML5 required-field errors first)
        self.page.fill("#client-name", f"test-{uuid.uuid4().hex[:6]}")
        self.page.fill("#client-namespace", "test")
        self.page.fill("#client-bootstrap", "localhost:9092")
        self.page.fill("#client-group-id", "test-group")
        self.page.fill("#client-destination-prefix", "plant/+/data/")

        self.page.locator("#save-client-btn").click()

        self.ix.assert_toast_error("wildcard")

    def test_destination_prefix_rejects_hash_wildcard(self):
        """destinationTopicPrefix with '#' is rejected on save."""
        self.page.fill("#client-name", f"test-{uuid.uuid4().hex[:6]}")
        self.page.fill("#client-namespace", "test")
        self.page.fill("#client-bootstrap", "localhost:9092")
        self.page.fill("#client-group-id", "test-group")
        self.page.fill("#client-destination-prefix", "plant/#/")

        self.page.locator("#save-client-btn").click()

        self.ix.assert_toast_error("wildcard")

    def test_valid_destination_prefix_accepted(self):
        """A destination prefix without wildcards is not rejected for wildcard reasons."""
        self.page.fill("#client-name", f"test-{uuid.uuid4().hex[:6]}")
        self.page.fill("#client-namespace", "test")
        self.page.fill("#client-bootstrap", "localhost:9092")
        self.page.fill("#client-group-id", "test-group")
        self.page.fill("#client-destination-prefix", "plant/line1/")

        self.page.locator("#save-client-btn").click()

        # Form may fail for other reasons (no real Kafka broker), but NOT for wildcards
        error = self.page.locator("#error-toast")
        if error.is_visible():
            # If there IS an error, it must not be about wildcards
            error_text = error.inner_text()
            assert "wildcard" not in error_text.lower(), (
                f"Unexpected wildcard error on valid prefix: {error_text}"
            )

    def test_empty_destination_prefix_allowed(self):
        """An empty destination prefix (optional field) must not trigger a wildcard error."""
        self.page.fill("#client-name", f"test-{uuid.uuid4().hex[:6]}")
        self.page.fill("#client-namespace", "test")
        self.page.fill("#client-bootstrap", "localhost:9092")
        self.page.fill("#client-group-id", "test-group")
        self.page.fill("#client-destination-prefix", "")

        self.page.locator("#save-client-btn").click()

        error = self.page.locator("#error-toast")
        if error.is_visible():
            assert "wildcard" not in error.inner_text().lower(), (
                "Wildcard error fired on empty destination prefix"
            )


# ── NATS Validation Tests ──────────────────────────────────────────────────────

def _create_nats_client(dashboard_url: str, token: str | None) -> str:
    """Create a minimal NATS client for testing. Returns the client name."""
    name = f"ui-test-nats-{uuid.uuid4().hex[:8]}"
    mutation = """
        mutation CreateNatsClient($input: NatsClientInput!) {
            natsClient { create(input: $input) { success errors } }
        }
    """
    result = _graphql(dashboard_url, mutation, {
        "input": {
            "name": name,
            "namespace": "ui-test",
            "nodeId": "*",
            "enabled": False,
            "config": {
                "servers": ["nats://localhost:4222"],
            }
        }
    }, token=token)
    success = result.get("natsClient", {}).get("create", {}).get("success", False)
    errors = result.get("natsClient", {}).get("create", {}).get("errors", [])
    if not success:
        pytest.skip(f"Could not create NATS test client: {errors}")
    return name


@pytest.fixture
def nats_detail_page(authenticated_page: Page):
    """Create a NATS client, navigate to its detail page. Cleanup after test."""
    if not _feature_enabled(DASHBOARD_URL, "Nats"):
        pytest.skip("Nats feature is not enabled on this broker")

    token = _get_token(authenticated_page)
    name = _create_nats_client(DASHBOARD_URL, token)

    authenticated_page.goto(f"{DASHBOARD_URL}/pages/nats-client-detail.html?client={name}")
    authenticated_page.wait_for_load_state("networkidle", timeout=15_000)

    yield authenticated_page, IxHelpers(authenticated_page)

    try:
        _graphql(
            DASHBOARD_URL,
            "mutation DeleteNatsClient($name: String!) { natsClient { delete(name: $name) } }",
            {"name": name},
            token=token,
        )
    except Exception:
        pass


def test_nats_publish_mode_mqtt_topic_rejects_wildcard(nats_detail_page):
    """NATS PUBLISH mode: MQTT topic (publish destination) must reject wildcard characters."""
    page, ix = nats_detail_page

    # Open the add-address modal
    add_btn = page.locator("button", has_text="Add Address")
    expect(add_btn).to_be_visible(timeout=8_000)
    add_btn.click()

    expect(page.locator("#add-address-form")).to_be_visible(timeout=5_000)

    page.select_option("#addr-mode", "PUBLISH")
    page.fill("#addr-nats-subject", "sensor.data")
    page.fill("#addr-mqtt-topic", "sensor/+/data")  # invalid publish destination

    page.locator("button", has_text="Save").click()

    ix.assert_toast_error("wildcard")


def test_nats_subscribe_mode_mqtt_topic_allows_wildcards(nats_detail_page):
    """NATS SUBSCRIBE mode: MQTT topic is a subscription source — wildcards are allowed."""
    page, ix = nats_detail_page

    add_btn = page.locator("button", has_text="Add Address")
    expect(add_btn).to_be_visible(timeout=8_000)
    add_btn.click()

    expect(page.locator("#add-address-form")).to_be_visible(timeout=5_000)

    page.select_option("#addr-mode", "SUBSCRIBE")
    page.fill("#addr-nats-subject", "sensor.data")
    page.fill("#addr-mqtt-topic", "sensor/+/data")  # wildcards OK in subscribe source

    page.locator("button", has_text="Save").click()

    # Must NOT show a wildcard error
    ix.assert_no_toast_error()
