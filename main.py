import os
import pickle
import random
import sqlite3
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
DB_FILE = Path(__file__).resolve().parent / "interacted_reels.db"


def _ensure_tracking_db() -> None:
    if not DB_FILE.exists():
        _initialize_tracking_db()


def _initialize_tracking_db() -> None:
    try:
        with sqlite3.connect(DB_FILE) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reels (
                    id INTEGER PRIMARY KEY,
                    reel_url TEXT UNIQUE,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
    except sqlite3.Error as exc:
        print(f"Tracking DB initialization failed: {exc}")


def is_reel_interacted(url: str) -> bool:
    try:
        _ensure_tracking_db()
        with sqlite3.connect(DB_FILE, timeout=5) as connection:
            cursor = connection.execute(
                "SELECT 1 FROM reels WHERE reel_url = ? LIMIT 1",
                (url,),
            )
            return cursor.fetchone() is not None
    except sqlite3.Error as exc:
        print(f"DB read failed for reel lookup: {exc}")
        return False


def log_interacted_reel(url: str) -> None:
    try:
        _ensure_tracking_db()
        with sqlite3.connect(DB_FILE, timeout=5) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO reels (reel_url) VALUES (?)",
                (url,),
            )
            connection.commit()
    except sqlite3.Error as exc:
        print(f"DB write failed while logging reel URL: {exc}")


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


def _open_new_unvisited_reel(
    driver: webdriver.Chrome,
    hashtag: str,
    max_scrolls: int = 10,
) -> bool:
    """Backward-compatible wrapper for the new click_reel flow."""
    return click_reel(driver, hashtag=hashtag, max_scrolls=max_scrolls)


def _like_opened_reel(driver: webdriver.Chrome, reel_url: str) -> None:
    """Like a Reel if the heart icon is currently not active."""
    like_locator_strategies = [
        (
            "SVG aria-label Like",
            (By.XPATH, "//svg[@aria-label='Like']/ancestor::button[1]"),
        ),
        (
            "Button containing Like text",
            (By.XPATH, "//button[.//svg[@aria-label='Like'] or contains(., 'Like')]")
        ),
        (
            "Role button aria-label Like",
            (By.CSS_SELECTOR, "button[aria-label='Like']"),
        ),
    ]

    for strategy_name, locator in like_locator_strategies:
        try:
            like_wait = WebDriverWait(driver, 10)
            like_button = like_wait.until(EC.element_to_be_clickable(locator))

            aria_label = (like_button.get_attribute("aria-label") or "").strip().lower()
            if "unlike" in aria_label or "liked" in aria_label:
                print(f"Reel already liked, skipping duplicate like: {reel_url}")
                return

            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", like_button)
            try:
                like_button.click()
            except (ElementClickInterceptedException, StaleElementReferenceException):
                refreshed_like_button = like_wait.until(EC.presence_of_element_located(locator))
                driver.execute_script("arguments[0].click();", refreshed_like_button)

            print(f"Successfully liked unique Reel: {reel_url} via {strategy_name}")
            return
        except TimeoutException:
            continue
        except StaleElementReferenceException:
            continue
        except Exception as exc:
            print(f"Like attempt failed for {reel_url} via {strategy_name}: {exc}")

    print(f"Could not locate a safe Like button for Reel: {reel_url}")


def click_reel(driver: webdriver.Chrome, hashtag: str = "simracing", max_scrolls: int = 10) -> bool:
    """Find, reserve, open, and like the first unvisited Reel on the current hashtag page."""
    db_existed_before_start = DB_FILE.exists()
    _ensure_tracking_db()

    if not db_existed_before_start:
        print("Tracking database not found yet; opening the first available Reel on the page.")

    hashtag_wait = WebDriverWait(driver, 10)
    scroll_attempts = 0

    while scroll_attempts < max_scrolls:
        try:
            hashtag_wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//a[contains(@href, '/reel/') or contains(@href, '/p/')]")
                )
            )
        except TimeoutException:
            print(f"No reel links loaded yet for #{hashtag}; attempting scroll {scroll_attempts + 1}/{max_scrolls}.")

        candidates = driver.find_elements(By.XPATH, "//a[contains(@href, '/reel/') or contains(@href, '/p/')]")
        visible_links: list[str] = []

        for candidate in candidates:
            try:
                if not candidate.is_displayed():
                    continue

                href = (candidate.get_attribute("href") or "").strip()
                if not href or href in visible_links:
                    continue
                visible_links.append(href)
            except StaleElementReferenceException:
                continue

        if visible_links and not db_existed_before_start:
            selected_url = visible_links[0]
            print(f"DB missing; selecting first available Reel: {selected_url}")
            try:
                first_link = driver.find_element(By.XPATH, f"//a[@href='{selected_url}']")
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", first_link)
                first_link.click()
            except Exception:
                driver.get(selected_url)

            log_interacted_reel(selected_url)
            time.sleep(random.uniform(1.2, 2.8))
            _like_opened_reel(driver, selected_url)
            return True

        for href in visible_links:
            if db_existed_before_start and is_reel_interacted(href):
                continue

            if db_existed_before_start:
                log_interacted_reel(href)
                print(f"Reserved new reel URL: {href}")
            else:
                print(f"Selected first available reel URL: {href}")

            # Small randomized pause before interaction to look less robotic.
            time.sleep(random.uniform(1.2, 2.8))

            try:
                clickable_link = driver.find_element(By.XPATH, f"//a[@href='{href}']")
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", clickable_link)
                clickable_link.click()
            except Exception:
                driver.get(href)

            print(f"Opened Reel from #{hashtag}: {href}")
            _like_opened_reel(driver, href)
            return True

        scroll_attempts += 1
        scroll_distance = random.randint(500, 900)
        driver.execute_script(
            "window.scrollTo({top: window.scrollY + arguments[0], behavior: 'smooth'});",
            scroll_distance,
        )
        time.sleep(random.uniform(1.0, 2.2))
        print(f"All visible reels already tracked. Scrolling ({scroll_attempts}/{max_scrolls}) for #{hashtag}.")

    print(f"No new reels found for #{hashtag} after {max_scrolls} scrolls. Switching hashtag.")
    return False


def _new_driver() -> webdriver.Chrome:
    os.environ.setdefault("SE_CACHE_PATH", str(Path(".selenium-cache").resolve()))
    options = Options()
    return webdriver.Chrome(options=options)


def MANAGE_COOKIES() -> None:
    _initialize_tracking_db()

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

        hashtags_to_try = ["simracing", "simracer", "simracingcommunity"]
        opened_new_reel = False
        for index, hashtag in enumerate(hashtags_to_try):
            if index == 0:
                _select_search(driver)
                _select_simracing_tag(driver)
            else:
                driver.get(f"https://www.instagram.com/explore/tags/{hashtag}/")
                time.sleep(random.uniform(1.5, 2.5))

            if click_reel(driver, hashtag=hashtag, max_scrolls=10):
                opened_new_reel = True
                break

        if not opened_new_reel:
            print("No brand-new reels found across configured hashtags.")

        _save_cookies(driver)

        print("Cookie session login successful.")
        input("Browser left open for inspection. Press Enter to close...")
    finally:
        driver.quit()


if __name__ == "__main__":
    MANAGE_COOKIES()
