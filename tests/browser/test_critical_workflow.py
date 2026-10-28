"""Critical workflow in a real browser: analyze -> evidence-backed findings -> approve one action -> mock result."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

IDLE = "i-0a1b2c3d4e5f67890"
VOL = "vol-0f1e2d3c4b5a69788"


def test_analyze_and_approve_stop_action(page: Page, live_server, screenshot_dir):
    page.goto(live_server)
    expect(page.get_by_test_id("fault-stale_billing")).to_be_visible()  # faults rendered from /api/faults

    page.get_by_test_id("analyze-button").click()
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "awaiting_approval")

    idle_row = page.get_by_test_id(f"finding-row-{IDLE}")
    vol_row = page.get_by_test_id(f"finding-row-{VOL}")
    expect(idle_row).to_contain_text("$571.39")
    expect(vol_row).to_contain_text("$160.00")
    expect(page.get_by_test_id("grounding-badge")).to_contain_text("figures match the data")
    expect(page.get_by_test_id("summary")).to_contain_text("$731.39")
    page.screenshot(path=str(screenshot_dir / "01-awaiting-approval.png"), full_page=True)

    page.get_by_test_id("action-checkbox-act_2").uncheck()  # approve only the stop action
    page.get_by_test_id("approve-button").click()
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "completed")
    expect(page.get_by_test_id("execution-act_1")).to_contain_text("stopped (mock)")
    expect(page.get_by_test_id("execution-act_2")).to_have_count(0)

    page.get_by_test_id("trace").locator("summary").click()
    expect(page.get_by_test_id("trace-row").filter(has_text="tool:get_cost_data")).to_have_count(1)
    page.screenshot(path=str(screenshot_dir / "02-approved.png"), full_page=True)
