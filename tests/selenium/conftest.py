"""
Shared setup for the Selenium tests (Lab 12).

Settings (environment variables):
  HOLOTUTOR_URL   address of the running app      (default http://localhost:8000)
  HEADLESS=1      run Chrome without a window
  SLOW=1          pause between steps, for the demo video
"""

from __future__ import annotations

import os
import time
import urllib.request

import pytest
from selenium import webdriver

BASE_URL = os.environ.get("HOLOTUTOR_URL", "http://localhost:8000").rstrip("/")
RESULTS: list[dict[str, str]] = []


def pause(seconds: float = 0.8) -> None:
    if os.environ.get("SLOW") == "1":
        time.sleep(seconds)


@pytest.fixture(scope="session")
def base_url() -> str:
    try:
        urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=5)
    except OSError:
        pytest.exit(f"The app is not running at {BASE_URL}. Start it first (start_holotutor.bat).", returncode=2)
    return BASE_URL


@pytest.fixture(scope="session")
def driver():
    options = webdriver.ChromeOptions()
    if os.environ.get("HEADLESS") == "1":
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    browser = webdriver.Chrome(options=options)
    yield browser
    browser.quit()


@pytest.fixture
def record():
    """Each test adds one row: input, expected, actual, pass/fail."""
    row: dict[str, str] = {}
    yield row
    if row:
        RESULTS.append(row)


def pytest_terminal_summary(terminalreporter):
    if not RESULTS:
        return
    tr = terminalreporter
    tr.section("Lab 12 Selenium results")
    head = f"{'Password':<22}{'Expected':<34}{'Actual':<52}{'Result'}"
    tr.write_line(head)
    tr.write_line("-" * len(head))
    for r in RESULTS:
        tr.write_line(f"{r['input']:<22}{r['expected']:<34}{r['actual'][:50]:<52}{r['result']}")
