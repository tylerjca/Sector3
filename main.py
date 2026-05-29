import os
import pickle
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import IG_PASSWORD, IG_USERNAME


COOKIE_FILE = Path(__file__).resolve().parent / "instagram_cookies.pkl"


def _click_if_present(wait: WebDriverWait, locator: tuple[str, str], label: str) -> bool:
    try:
        button = wait.until(EC.element_to_be_clickable(locator))
        button.click()
        print(f"Clicked popup action: {label}")
        return True
    except TimeoutException:
        return False


def _type_with_fallback(driver: webdriver.Chrome, element, value: str, label: str) -> None:
    try:
        element.click()
        element.clear()
        element.send_keys(value)
        return
    except Exception:
        # Fallback for dynamic inputs that reject immediate key typing.
        driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
            element,
            value,
        )
        print(f"Used JS fallback typing for {label} field.")


def _fill_input_verified(
    driver: webdriver.Chrome,
    wait: WebDriverWait,
    locator: tuple[str, str],
    value: str,
    label: str,
    retries: int = 3,
) -> None:
    for attempt in range(1, retries + 1):
        field = wait.until(EC.element_to_be_clickable(locator))
        field.click()
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(Keys.BACKSPACE)
        _type_with_fallback(driver, field, value, label)

        typed_value = (field.get_attribute("value") or "").strip()
        if typed_value:
            print(f"{label} field populated on attempt {attempt}.")
            return

    raise RuntimeError(f"Failed to populate {label} after {retries} attempts.")


def _handle_notifications_popup(driver: webdriver.Chrome) -> None:
    try:
        # Instagram re-renders this modal frequently; keep multiple locator fallbacks.
        locator_strategies = [
            (
                "Flexible Relative XPath",
                (
                    By.XPATH,
                    "//button[contains(normalize-space(.), 'Not Now')]"
                    " | //button[.//div[contains(normalize-space(.), 'Not Now')]]"
                    " | //button[.//span[contains(normalize-space(.), 'Not Now')]]",
                ),
            ),
            (
                "Attribute Targeting",
                (
                    By.XPATH,
                    "//button[@class and contains(normalize-space(.), 'Not Now')]",
                ),
            ),
            (
                "Absolute Backup XPath",
                (
                    By.XPATH,
                    "/html/body/div[2]/div[1]/div/div[2]/div/div/div/div/div[2]/div/div/div[3]/button[2]",
                ),
            ),
        ]

        for strategy_name, locator in locator_strategies:
            try:
                popup_wait = WebDriverWait(driver, 5)
                popup_wait.until(EC.presence_of_element_located(locator))
                time.sleep(6)

                # Re-locate after delay to avoid stale references on dynamic DOM updates.
                not_now_button = popup_wait.until(EC.element_to_be_clickable(locator))
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", not_now_button)
                try:
                    not_now_button.click()
                except (ElementClickInterceptedException, StaleElementReferenceException):
                    # Final fallback when overlays or re-renders intercept normal click.
                    refreshed_button = popup_wait.until(EC.presence_of_element_located(locator))
                    driver.execute_script("arguments[0].click();", refreshed_button)

                print(f"Turn on Notifications dismissed via {strategy_name}.")
                return
            except TimeoutException:
                continue

        print("Turn on Notifications pop-up skipped (not shown).")
    except Exception:
        print("Turn on Notifications pop-up skipped (handled safely).")


def LOGIN() -> None:
    # Use a project-local cache so Selenium Manager can download the right driver.
    os.environ.setdefault("SE_CACHE_PATH", str(Path(".selenium-cache").resolve()))

    options = Options()
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 20)

    driver.get("https://instagram.com")
    print("Opened Instagram login page.")

    # Cookie consent can block the login form on first visit.
    consent_wait = WebDriverWait(driver, 8)
    _click_if_present(
        consent_wait,
        (
            By.XPATH,
            "//button[contains(., 'Allow all cookies') or contains(., 'Allow all') or contains(., 'Only allow essential cookies') or contains(., 'Decline optional cookies')]",
        ),
        "Cookie consent",
    )

    username_locator = (By.XPATH, "//*[@id='_r_4_']")
    password_locator = (By.XPATH, "//*[@id='_r_7_']")

    _fill_input_verified(driver, wait, username_locator, IG_USERNAME, "username")
    _fill_input_verified(driver, wait, password_locator, IG_PASSWORD, "password")

    # Pressing Tab helps trigger client-side validation in some UI builds.
    password_input = wait.until(EC.element_to_be_clickable(password_locator))
    password_input.send_keys(Keys.TAB)

    login_button = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//button[@type='submit' and (.//div[text()='Log in'] or normalize-space()='Log in')]",
            )
        )
    )
    login_button.click()
    print("Submitted login form.")

    # Instagram may show one-tap save prompt after successful sign-in.
    short_wait = WebDriverWait(driver, 6)
    _click_if_present(
        short_wait,
        (By.XPATH, "//button[contains(., 'Not now') or contains(., 'Not Now') ]"),
        "Not Now (Save Info)",
    )

    wait.until(
        lambda d: any(
            d.find_elements(*locator)
            for locator in [
                (By.CSS_SELECTOR, "svg[aria-label='Search']"),
                (By.CSS_SELECTOR, "a[href='/']"),
                (By.CSS_SELECTOR, "a[href='/explore/']"),
                (By.CSS_SELECTOR, "a[href*='accounts/edit']"),
            ]
        )
    )

    _handle_notifications_popup(driver)

    print("Login verification passed: post-login navigation elements detected.")
    input("Browser left open for inspection. Press Enter to close...")
    driver.quit()


def _is_logged_in(driver: webdriver.Chrome, timeout: int = 20) -> bool:
    wait = WebDriverWait(driver, timeout)
    try:
        wait.until(
            lambda d: any(
                d.find_elements(*locator)
                for locator in [
                    (By.CSS_SELECTOR, "svg[aria-label='Search']"),
                    (By.CSS_SELECTOR, "a[href='/explore/']"),
                    (By.CSS_SELECTOR, "a[href*='accounts/edit']"),
                    (By.CSS_SELECTOR, "a[href*='/direct/inbox/']"),
                ]
            )
        )
    except TimeoutException:
        return False

    return not driver.find_elements(By.CSS_SELECTOR, "input[name='username']")


def _normalize_cookie(cookie: dict) -> dict:
    allowed_keys = {
        "name",
        "value",
        "path",
        "domain",
        "secure",
        "httpOnly",
        "expiry",
        "sameSite",
    }
    normalized = {k: v for k, v in cookie.items() if k in allowed_keys}
    if "expiry" in normalized:
        normalized["expiry"] = int(normalized["expiry"])
    return normalized


def _save_cookies(driver: webdriver.Chrome) -> None:
    with COOKIE_FILE.open("wb") as file_obj:
        pickle.dump(driver.get_cookies(), file_obj)
    print(f"Saved cookies to {COOKIE_FILE.name}")


def _load_cookies() -> list[dict]:
    with COOKIE_FILE.open("rb") as file_obj:
        return pickle.load(file_obj)


def _select_search(driver: webdriver.Chrome) -> None:
    """Click the Search navigation element (depends on notifications popup being dismissed)."""
    locator_strategies = [
        (
            "Search link via SVG label",
            (By.XPATH, "//a[.//svg[@aria-label='Search'] or .//title[normalize-space()='Search']]")
        ),
        (
            "Search button via SVG label",
            (By.XPATH, "//button[.//svg[@aria-label='Search'] or .//title[normalize-space()='Search']]")
        ),
        (
            "Explore link fallback",
            (By.XPATH, "//a[contains(@href, '/explore/') and (.//svg or contains(normalize-space(.), 'Search'))]")
        ),
        (
            "Search text fallback",
            (By.XPATH, "//*[self::a or self::button or @role='button'][contains(normalize-space(.), 'Search')]")
        ),
    ]

    for strategy_name, locator in locator_strategies:
        try:
            search_wait = WebDriverWait(driver, 10)
            clickable_target = search_wait.until(EC.element_to_be_clickable(locator))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", clickable_target)
            try:
                clickable_target.click()
            except (ElementClickInterceptedException, StaleElementReferenceException):
                refreshed_target = search_wait.until(EC.presence_of_element_located(locator))
                driver.execute_script("arguments[0].click();", refreshed_target)

            print(f"Search selected via {strategy_name}.")
            return
        except TimeoutException:
            continue

    print("Search element not found — skipping.")


def _select_simracing_tag(driver: webdriver.Chrome) -> None:
    """Click the SimRacing search result after Search has been opened."""
    locator_strategies = [
        (
            "Provided absolute XPath",
            (
                By.XPATH,
                "//*[@id='mount_0_0_Du']/div/div/div[2]/div/div/div[1]/div[1]/div[1]/div/div/div/div/div/div/div/div[2]/div[2]/div/div/div[2]/div/div/ul/div/a/div[1]/div/div",
            ),
        ),
        (
            "Result row contains simracing",
            (
                By.XPATH,
                "//a[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'simracing')]]",
            ),
        ),
    ]

    for strategy_name, locator in locator_strategies:
        try:
            tag_wait = WebDriverWait(driver, 12)
            element = tag_wait.until(EC.presence_of_element_located(locator))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)

            try:
                clickable_target = element.find_element(By.XPATH, "./ancestor::a[1] | ./ancestor::*[@role='button'][1]")
            except Exception:
                clickable_target = element

            try:
                tag_wait.until(lambda _d: clickable_target.is_displayed() and clickable_target.is_enabled())
                clickable_target.click()
            except Exception:
                # Final fallback: dispatch a real mouse click event in case direct click is intercepted.
                driver.execute_script(
                    "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));",
                    clickable_target,
                )

            print(f"SimRacing tag selected via {strategy_name}.")
            return
        except TimeoutException:
            continue
        except StaleElementReferenceException:
            continue

    print("SimRacing tag not found or not clickable — skipping.")


def _new_driver() -> webdriver.Chrome:
    os.environ.setdefault("SE_CACHE_PATH", str(Path(".selenium-cache").resolve()))
    options = Options()
    return webdriver.Chrome(options=options)


def MANAGE_COOKIES() -> None:
    # Bootstrap cookie file with manual login if it does not exist yet.
    if not COOKIE_FILE.exists():
        bootstrap_driver = _new_driver()
        try:
            bootstrap_driver.get("https://instagram.com")
            print("Cookie file not found.")
            print("Please log in manually in this browser and complete any 2FA challenge.")
            input("Press Enter after the Instagram home/dashboard is visible...")

            if not _is_logged_in(bootstrap_driver, timeout=30):
                raise RuntimeError("Manual login was not detected. Please complete login/2FA and retry.")

            _handle_notifications_popup(bootstrap_driver)
            _save_cookies(bootstrap_driver)
        finally:
            bootstrap_driver.quit()

    # Restore session from saved cookies and keep browser open for verification.
    driver = _new_driver()
    try:
        driver.get("https://instagram.com")

        try:
            cookies = _load_cookies()
        except (EOFError, pickle.UnpicklingError):
            print("Cookie file is empty or corrupted — deleting and re-running bootstrap.")
            COOKIE_FILE.unlink(missing_ok=True)
            driver.quit()
            MANAGE_COOKIES()
            return

        for cookie in cookies:
            try:
                driver.add_cookie(_normalize_cookie(cookie))
            except Exception:
                # Skip invalid/stale cookies and keep trying the remaining ones.
                continue

        driver.refresh()

        if not _is_logged_in(driver, timeout=20):
            raise RuntimeError("Cookie login failed: login form is still visible.")

        _handle_notifications_popup(driver)
        _select_search(driver)
        _select_simracing_tag(driver)
        _save_cookies(driver)

        print("Cookie session login successful.")
        input("Browser left open for inspection. Press Enter to close...")
    finally:
        driver.quit()


if __name__ == "__main__":
    MANAGE_COOKIES()
