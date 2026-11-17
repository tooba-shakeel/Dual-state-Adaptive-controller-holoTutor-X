"""
Lab 12: Selenium WebDriver checks of the Sign Up password rule (8 to 64 characters).

Five boundary inputs, like the lab's -1, 0, 5, 10, 11:
one below the lower edge, the lower edge, a middle value, the upper edge, one above it.
Two extra checks follow Copilot's review finding: an emoji is one character,
so the form must count it the same way as the server.

Run (with the app running):
    pip install -r tests/selenium/requirements.txt
    python -m pytest tests/selenium -v
"""

from __future__ import annotations

import uuid

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from conftest import pause

RANGE_MESSAGE = "Password must be 8 to 64 characters"


def open_sign_up(driver, base_url):
    """Fresh page with nobody logged in, then switch the login card to Sign Up."""
    driver.get(base_url)
    driver.execute_script("localStorage.clear();")
    driver.get(base_url)
    wait = WebDriverWait(driver, 10)
    wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "Create an account"))).click()
    wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".auth-overlay .auth-form")))
    pause()


def fill(driver, element, text):
    """Type plain text; set emoji with JavaScript (ChromeDriver cannot type characters outside the BMP)."""
    element.clear()
    if all(ord(c) <= 0xFFFF for c in text):
        element.send_keys(text)
    else:
        driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
            element, text,
        )


def sign_up(driver, base_url, password):
    """Fill the Sign Up form and return ('accepted', '') or ('rejected', message)."""
    open_sign_up(driver, base_url)
    name, email, pw, confirm = driver.find_elements(By.CSS_SELECTOR, ".auth-overlay .auth-form input")
    fill(driver, name, "Selenium Student")
    fill(driver, email, f"selenium.{uuid.uuid4().hex[:8]}@test.local")
    fill(driver, pw, password)
    fill(driver, confirm, password)
    pause()
    driver.find_element(By.CSS_SELECTOR, ".auth-overlay .auth-submit").click()

    def outcome(d):
        error = d.find_element(By.CSS_SELECTOR, ".auth-overlay .auth-error")
        if error.is_displayed() and error.text.strip():
            return ("rejected", error.text.strip())
        if d.find_elements(By.CSS_SELECTOR, ".auth-user__name"):
            overlay = d.find_element(By.CSS_SELECTOR, ".auth-overlay")
            if not overlay.is_displayed():
                return ("accepted", "")
        return False

    result = WebDriverWait(driver, 10).until(outcome)
    pause(1.2)
    return result


# (label, password, should be accepted)
BOUNDARY_CASES = [
    ("7 characters", "a" * 7, False),
    ("8 characters", "a" * 8, True),
    ("20 characters", "a" * 20, True),
    ("64 characters", "a" * 64, True),
    ("65 characters", "a" * 65, False),
]

# Copilot finding: the form must count an emoji as one character, like the server.
EMOJI_CASES = [
    ("4 emoji", "\U0001F600" * 4, False),
    ("33 emoji", "\U0001F600" * 33, True),
]


def check(driver, base_url, record, label, password, should_accept, rejected_by_form):
    status, message = sign_up(driver, base_url, password)
    expected = "Accepted" if should_accept else "Rejected by the form"
    actual = "Accepted" if status == "accepted" else f"Rejected: {message}"
    if should_accept:
        ok = status == "accepted"
    else:
        # The form's own message ends with a full stop; the server's does not.
        ok = status == "rejected" and RANGE_MESSAGE in message and (
            message.endswith(".") or not rejected_by_form
        )
    record.update(input=label, expected=expected, actual=actual, result="PASS" if ok else "FAIL")
    assert ok, f"{label}: expected {expected}, got {actual}"


@pytest.mark.parametrize("label, password, should_accept", BOUNDARY_CASES, ids=[c[0] for c in BOUNDARY_CASES])
def test_password_length_boundaries(driver, base_url, record, label, password, should_accept):
    check(driver, base_url, record, label, password, should_accept, rejected_by_form=True)


@pytest.mark.parametrize("label, password, should_accept", EMOJI_CASES, ids=[c[0] for c in EMOJI_CASES])
def test_emoji_counted_like_the_server(driver, base_url, record, label, password, should_accept):
    check(driver, base_url, record, label, password, should_accept, rejected_by_form=True)
