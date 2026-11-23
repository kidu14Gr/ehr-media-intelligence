"""Headless browser smoke test for clinical search and drawer interactions.

Validates:
1. Load / (SPA shell loads with title and assets)
2. Search query execution and result card rendering
3. Opening the patient detail drawer
4. Pressing Escape to close the drawer and verifying focus returns to triggering element
"""
import socket
import subprocess
import time
from pathlib import Path

import pytest
import requests


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    """Starts live uvicorn server in background subprocess for browser tests."""
    port = get_free_port()
    backend_dir = Path(__file__).resolve().parent.parent.parent
    python_bin = backend_dir / ".venv" / "bin" / "python"

    proc = subprocess.Popen(
        [
            str(python_bin),
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(backend_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    url = f"http://127.0.0.1:{port}"

    # Poll /health until server is up (up to 30s for embedding model preload)
    start_time = time.time()
    server_ready = False
    while time.time() - start_time < 30:
        try:
            r = requests.get(f"{url}/health", timeout=1)
            if r.status_code == 200:
                server_ready = True
                break
        except Exception:
            time.sleep(0.5)

    if not server_ready:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(f"Server failed to start on port {port}. Stdout: {stdout}, Stderr: {stderr}")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_browser_search_and_drawer_interaction(live_server):
    """End-to-end browser smoke test with Chromium."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed in environment.")

    chrome_bin = "/usr/bin/google-chrome"
    import os
    if not os.path.exists(chrome_bin):
        pytest.skip("Google Chrome not available on system.")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chrome_bin,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()

        # 1. Load /
        page.goto(live_server)
        assert "EHR Media Intelligence" in page.title()

        # 2. Search for cohort query
        search_input = page.locator("#clinical-search-input")
        search_input.fill("lumbar spine MRI")
        page.keyboard.press("Enter")

        # 3. Wait for results to render
        page.wait_for_selector("article[tabindex='0']", timeout=15000)
        cards = page.locator("article[tabindex='0']")
        assert cards.count() >= 1

        first_card = cards.first
        card_text = first_card.text_content()
        assert "Maria Garcia" in card_text
        assert "Score:" in card_text

        # 4. Open patient drawer
        view_btn = first_card.locator("button:has-text('View Patient Drawer')")
        view_btn.focus()
        view_btn.click()

        # 5. Verify drawer opened
        page.wait_for_selector("#patient-drawer:not(.translate-x-full)", timeout=8000)
        patient_title = page.locator("#drawer-patient-name")
        assert "Maria Garcia" in patient_title.text_content()

        # Check AI disclaimer is present
        assert page.locator("#drawer-disclaimer-text").is_visible()

        # 6. Press Escape to close drawer
        page.keyboard.press("Escape")
        page.wait_for_selector("#patient-drawer.translate-x-full", timeout=5000)

        # 7. Verify focus returns to the triggering button
        page.wait_for_timeout(200)
        is_focused = view_btn.evaluate("el => document.activeElement === el")
        assert is_focused is True, "Expected view_btn to have regained focus on drawer close."

        browser.close()
