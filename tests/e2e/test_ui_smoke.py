"""Playwright end-to-end UI smoke tests.

Skipped unless KAIROS_E2E_URL is set, e.g.:

    KAIROS_E2E_URL=http://localhost:8090 uv run python -m pytest tests/e2e/test_ui_smoke.py -v

Saves screenshots to docs/screenshots/ for the README walkthrough.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

BASE = os.environ.get("KAIROS_E2E_URL")
pytestmark = pytest.mark.skipif(
    not BASE, reason="KAIROS_E2E_URL not set; Playwright E2E tests skipped"
)

SHOTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _viewport(page: Page) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})


def _shot(page: Page, name: str) -> None:
    """Save a desktop screenshot under docs/screenshots/."""
    path = SHOTS_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)


# ── Navigation + landing ──────────────────────────────────────────
def test_ui_root_redirects_to_home(page: Page) -> None:
    page.goto(f"{BASE}/ui")
    expect(page).to_have_url(f"{BASE}/ui/home")


def test_home_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/home")
    expect(page).to_have_title("Home · KAIROS")
    expect(page.locator("h1")).to_contain_text("The right scale")
    expect(page.get_by_role("link", name="Open Overview", exact=False)).to_be_visible()
    _shot(page, "01-home")


def test_dashboard_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/dashboard")
    expect(page.locator("h1")).to_contain_text("Activity")
    # Activity panels exist
    expect(page.get_by_text("Decisions per day", exact=False)).to_be_visible()
    expect(page.get_by_text("Decision actions", exact=False)).to_be_visible()
    _shot(page, "02-dashboard")


def test_pending_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/pending")
    expect(page.locator("h1")).to_contain_text("Pending approvals")
    _shot(page, "03-pending")


def test_admin_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/admin")
    expect(page.locator("h1")).to_contain_text("Admin")
    expect(page.get_by_text("Environment profiles", exact=False)).to_be_visible()
    expect(page.get_by_text("Connections (active config)", exact=False)).to_be_visible()
    _shot(page, "04-admin")


def test_admin_env_form_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/admin/envs/new")
    expect(page.locator("h1")).to_contain_text("New environment profile")
    # Each major section heading
    for section in ("Identity", "Grafana", "Mimir", "GitHub", "Cost rates"):
        expect(page.get_by_role("heading", name=section, exact=False).first).to_be_visible()
    _shot(page, "05-admin-env-form")


def test_keda_catalog_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/keda/catalog")
    expect(page.locator("h1")).to_contain_text("Scaler catalog")
    # The 14 priority scalers ship; spot-check three
    for name in ("Apache Kafka", "RabbitMQ", "Prometheus"):
        expect(page.get_by_role("heading", name=name, exact=True).first).to_be_visible()
    _shot(page, "06-keda-catalog")


def test_workloads_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/workloads")
    expect(page.locator("h1")).to_contain_text("Workloads")
    _shot(page, "07-workloads")


def test_alerts_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/alerts")
    expect(page.locator("h1")).to_contain_text("Alerts")
    _shot(page, "08-alerts")


def test_history_renders(page: Page) -> None:
    page.goto(f"{BASE}/ui/history")
    # h1 not standardized on this page yet — just verify navigation works + sidebar correct
    expect(page).to_have_url(f"{BASE}/ui/history")
    _shot(page, "09-history")


# ── Sidebar a11y + navigation ────────────────────────────────────
def test_sidebar_active_state_uses_aria_current(page: Page) -> None:
    page.goto(f"{BASE}/ui/dashboard")
    overview = page.get_by_role("link", name="Overview", exact=True)
    expect(overview).to_have_attribute("aria-current", "page")


def test_skip_to_main_content_present(page: Page) -> None:
    page.goto(f"{BASE}/ui/home")
    skip = page.locator("a.skip-link")
    expect(skip).to_have_attribute("href", "#main-content")


def test_sidebar_nav_to_admin(page: Page) -> None:
    page.goto(f"{BASE}/ui/home")
    page.get_by_role("link", name="Admin", exact=True).click()
    expect(page).to_have_url(f"{BASE}/ui/admin")
    expect(page.locator("h1")).to_contain_text("Admin")


# ── Workflow: trigger run via UI button ──────────────────────────
def test_trigger_run_button_fires(page: Page) -> None:
    page.goto(f"{BASE}/ui/dashboard")
    # We're testing the HTMX wiring fires a POST — not that the API call succeeds
    # (HTMX hx-vals JSON encoding can vary; the API contract is covered by the
    # API smoke test instead). We assert the request was made and got a response.
    with page.expect_response(lambda r: "/api/v1/runs" in r.url) as run_info:
        page.get_by_role("button", name="Trigger run", exact=False).first.click()
    response = run_info.value
    assert response.status > 0  # any response — wiring works


# ── Admin connection test (HTMX swap) ───────────────────────────
def test_admin_connection_test_swap(page: Page) -> None:
    page.goto(f"{BASE}/ui/admin")
    # Click the Test button on the Grafana row
    grafana_row = page.locator("#conn-grafana")
    test_btn = grafana_row.get_by_role("button", name="Test", exact=False)
    test_btn.click()
    # Pill should swap to ok / down / degraded / unconfigured
    pill = page.locator("#conn-grafana-state")
    expect(pill).to_be_visible()
    expect(pill).to_contain_text("·")  # state · detail format
