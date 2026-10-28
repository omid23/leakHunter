"""Live server (uvicorn in a thread, scripted LLM, free port) for the Playwright browser tests."""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from leakhunter.api.app import create_app
from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parents[2]
# Screenshots land in the git-ignored test-results/ by default; point LEAKHUNTER_SCREENSHOT_DIR at
# reports/screenshots to refresh the committed copies.
SCREENSHOTS = Path(os.environ.get("LEAKHUNTER_SCREENSHOT_DIR") or ROOT / "test-results" / "screenshots")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_server():
    port = _free_port()
    app = create_app(make_settings(tool_timeout_s=0.2, fault_timeout_sleep_s=0.5))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/api/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        raise RuntimeError("live server did not start")
    yield base_url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Allow a pre-installed Chromium (LEAKHUNTER_CHROMIUM_PATH) when Playwright's own build is absent."""
    executable = os.environ.get("LEAKHUNTER_CHROMIUM_PATH")
    return {**browser_type_launch_args, **({"executable_path": executable} if executable else {})}


@pytest.fixture
def screenshot_dir() -> Path:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS
