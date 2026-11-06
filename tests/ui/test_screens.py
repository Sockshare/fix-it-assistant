"""G4 — UI render test (Playwright). Full click-through in No-AI mode; zero console errors; no text leaks; responsive."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SHOTS = ROOT / "docs" / "screenshots"
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
pytestmark = pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    env = {**os.environ, "FIXIT_DATA_DIR": str(tmp_path_factory.mktemp("data")), "FIXIT_PROVIDER": "offline"}
    proc = subprocess.Popen([sys.executable, "app.py", "--port", str(port), "--no-browser"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _collect(page):
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    # Chrome logs a console line for every non-2xx response; the intentional bad-file 400 is covered by the response hook below.
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("response", lambda r: errors.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)
    return errors


def _no_leaks(page):
    body = page.inner_text("body")
    for bad in ("undefined", "[object Object]", "NaN"):
        assert bad not in body, bad
    assert not re.search(r"\bnull\b", body)


def test_full_flow(server, browser):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    errors = _collect(page)
    page.goto(server)
    page.wait_for_selector("#screen-setup:not([hidden])", timeout=10000)
    assert page.locator(".choice").count() == 3
    page.screenshot(path=SHOTS / "01_first_run.png", full_page=True)
    page.click('.choice[data-choice="offline"]')
    page.wait_for_selector("#screen-input:not([hidden])")
    assert page.locator("#areaChips .chip").count() >= 8
    # validation first
    page.click("#goBtn")
    page.wait_for_selector("#toast:not([hidden])")
    assert "tell me" in page.inner_text("#toast").lower()
    page.click("[data-example*='toilet']")
    page.click("[data-area=plumbing]")
    page.screenshot(path=SHOTS / "02_describe.png", full_page=True)
    page.click("#goBtn")
    page.wait_for_selector("#screen-results:not([hidden])", timeout=15000)
    page.wait_for_selector("#cardWrap:not([hidden])")
    assert "Toilet keeps running" in page.inner_text("#cardTitle")
    assert "Safe to try" in page.inner_text("#safetyLabel")
    assert page.locator("#steps li").count() >= 3 and page.locator("#causes .cause").count() >= 3
    assert page.locator("#tools li").count() >= 1 and page.locator("#otherMatches .chip").count() >= 1
    page.screenshot(path=SHOTS / "03_result_top.png")
    page.screenshot(path=SHOTS / "04_result_full.png", full_page=True)
    # follow-up answer re-ranks causes
    first = page.inner_text(".cause.top .cause-name")
    page.click("[data-q=pattern][data-o*='All the time']")
    page.wait_for_function(f"document.querySelector('.cause.top .cause-name').innerText !== {first!r}")
    assert "fits your answers" in page.inner_text(".cause.top")
    # switch to another match and back
    page.click("#otherMatches .chip >> nth=0")
    page.wait_for_function("document.getElementById('cardTitle').innerText !== 'Toilet keeps running'")
    # ask (rules mode)
    page.fill("#askText", "how much will it cost?")
    page.click("#askBtn")
    page.wait_for_selector(".msg.assistant", timeout=15000)
    assert "$" in page.inner_text(".msg.assistant")
    # status + notebook
    page.select_option("#rStatus", "fixed")
    page.wait_for_selector("#toast:not([hidden])")
    page.click("#notebookBtn")
    page.wait_for_selector("#modal-notebook:not([hidden])")
    assert page.locator(".app-row").count() == 1 and "fixed" in page.inner_text(".app-row").lower()
    page.screenshot(path=SHOTS / "08_notebook.png")
    page.click(".app-row [data-open]")
    page.wait_for_selector("#modal-notebook", state="hidden")
    assert page.locator(".msg.assistant").count() == 1  # chat restored
    # emergency path
    page.click("#backBtn")
    page.fill("#descText", "I smell gas near the water heater and the pilot light is out")
    page.click("#goBtn")
    page.wait_for_selector("#flags:not([hidden])", timeout=15000)
    assert "gas" in page.inner_text("#flags").lower() and "do this first" in page.inner_text("#flags").lower()
    page.screenshot(path=SHOTS / "05_emergency.png", full_page=True)
    # no-match path
    page.click("#backBtn")
    page.fill("#descText", "my printer is jammed again today")
    page.click("#goBtn")
    page.wait_for_selector("#screen-results:not([hidden])")
    page.wait_for_timeout(300)
    assert page.is_visible("#noMatch") or page.is_visible("#cardWrap")
    # browse
    page.click("#browseBtn")
    page.wait_for_selector("#modal-browse:not([hidden])")
    assert page.locator(".browse-item").count() >= 40
    page.fill("#browseFilter", "squeak")
    page.wait_for_function("document.querySelectorAll('.browse-item').length < 10")
    page.screenshot(path=SHOTS / "06_browse.png")
    page.click(".browse-item >> nth=0")
    page.wait_for_selector("#modal-browse", state="hidden")
    page.wait_for_function("document.getElementById('cardTitle').innerText.toLowerCase().includes('squeak')")
    # help + settings
    page.click("#helpBtn")
    page.wait_for_selector("#modal-help:not([hidden])")
    page.keyboard.press("Escape")
    page.click("#settingsBtn")
    page.wait_for_selector("#modal-settings:not([hidden])")
    page.click('input[name=prov][value=gemini]')
    page.click("#setTest")
    page.wait_for_function("document.getElementById('setTestOut').textContent.includes('\u2717')")
    page.click('input[name=prov][value=ollama]')
    assert page.locator("#setVisionModel option").count() >= 1
    page.click('input[name=prov][value=offline]')
    page.click("#setSave")
    page.wait_for_selector("#modal-settings", state="hidden")
    page.screenshot(path=SHOTS / "07_settings.png")
    _no_leaks(page)
    assert errors == [], errors
    page.close()


@pytest.mark.parametrize("width,name", [(1024, "laptop"), (390, "phone")])
def test_responsive(server, browser, width, name):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = _collect(page)
    page.goto(server)
    page.wait_for_selector("#screen-input:not([hidden]), #screen-setup:not([hidden])", timeout=10000)
    if page.is_visible("#screen-setup"):
        page.click('.choice[data-choice="offline"]')
        page.wait_for_selector("#screen-input:not([hidden])")
    for sel in ("#screen-input",):
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 2, f"{sel} overflows {overflow}px at {width}px"
    page.click("[data-example*='outlet']")
    page.click("#goBtn")
    page.wait_for_selector("#cardWrap:not([hidden])", timeout=15000)
    page.wait_for_timeout(300)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 2, f"results overflow {overflow}px at {width}px"
    page.screenshot(path=SHOTS / f"09_result_{name}.png", full_page=True)
    assert errors == [], errors
    page.close()
