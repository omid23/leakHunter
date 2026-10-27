"""Failure paths are visible in the UI: unsafe request refused, stale data reported, nothing to approve."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser


def test_unsafe_request_is_refused_in_the_ui(page: Page, live_server, screenshot_dir):
    page.goto(live_server)
    page.get_by_test_id("unsafe-button").click()
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "refused")
    expect(page.get_by_test_id("refusal-banner")).to_contain_text("Refused")
    expect(page.get_by_test_id("approve-button")).to_be_hidden()
    expect(page.get_by_test_id("trace-row")).to_have_count(2)  # safety check + transition, no tools
    page.screenshot(path=str(screenshot_dir / "03-unsafe-refused.png"), full_page=True)


def test_stale_billing_shows_error_banner(page: Page, live_server, screenshot_dir):
    page.goto(live_server)
    page.get_by_test_id("fault-stale_billing").check()
    page.get_by_test_id("analyze-button").click()
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "failed")
    expect(page.get_by_test_id("error-banner")).to_contain_text("stale_data")
    expect(page.get_by_test_id("error-banner")).to_contain_text("2026-10-20")
    expect(page.get_by_test_id("actions")).to_be_hidden()
    page.screenshot(path=str(screenshot_dir / "04-stale-billing.png"), full_page=True)


def test_tool_timeout_shows_warning_and_no_actions(page: Page, live_server):
    page.goto(live_server)
    page.get_by_test_id("fault-tool_timeout").check()
    page.get_by_test_id("analyze-button").click()
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "completed")
    expect(page.get_by_test_id("warnings")).to_contain_text("timeout")
    expect(page.get_by_test_id("actions")).to_be_hidden()
