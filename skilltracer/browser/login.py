"""Log in to the public WebArena benchmark accounts."""

import logging

from skilltracer.config import SITE_URLS

from .observations import extract_all_elements

logger = logging.getLogger(__name__)
ACCOUNTS = {
    "shopping": {"username": "emma.lopez@gmail.com", "password": "Password.123"},
    "shopping_admin": {"username": "admin", "password": "admin1234"},
    "reddit": {"username": "MarvelsGrantMan136", "password": "test1234"},
    "gitlab": {"username": "byteblaze", "password": "hello1234"},
}
LOGIN_FORMS = {
    "shopping": ("/customer/account/login/", ["email"], ["sign in", "login"]),
    "shopping_admin": ("/admin", ["user name", "username"], ["sign in", "login"]),
    "reddit": ("/login", ["username"], ["log in", "login"]),
    "gitlab": ("/users/sign_in", ["username"], ["sign in"]),
}


def locate_element_by_value(elements, search_values, role=None):
    for value in search_values:
        for element in elements:
            if role is not None and element.get("role") != role:
                continue
            if value.lower() in element.get("value", "").lower():
                return element["id"]
    return None


def login_site_with_env(browser, site):
    path, username_names, button_names = LOGIN_FORMS[site]
    origin = SITE_URLS[site].removesuffix("/admin") if site == "shopping_admin" else SITE_URLS[site]
    succeeded, _, _ = browser.execute_browsergym_action(f"goto({(origin + path)!r})")
    if not succeeded:
        return False
    elements = extract_all_elements(browser.get_page_state()["axtree_object"])
    username = locate_element_by_value(elements, username_names, role="textbox")
    password = next(
        (
            element["id"]
            for element in elements
            if element.get("role") == "textbox"
            and any(
                isinstance(attribute, dict)
                and attribute.get("name") == "type"
                and attribute.get("value") == "password"
                for attribute in element.get("attributes", [])
            )
        ),
        None,
    )
    password = password or locate_element_by_value(elements, ["password"], role="textbox")
    button = locate_element_by_value(elements, button_names, role="button")
    if not all((username, password, button)):
        logger.error("Could not locate login fields for %s.", site)
        return False
    account = ACCOUNTS[site]
    for action in [
        f"fill({username!r}, {account['username']!r})",
        f"fill({password!r}, {account['password']!r})",
        f"click({button!r})",
    ]:
        succeeded, _, _ = browser.execute_browsergym_action(action)
        if not succeeded:
            return False
    page = browser.env.unwrapped.page

    def predicate(url):
        if site == "shopping_admin":
            return "/admin/" in urlsplit_path(url)
        return path.rstrip("/") not in urlsplit_path(url)

    page.wait_for_url(predicate, wait_until="domcontentloaded")
    browser.refresh()
    logger.info("Logged in to %s.", site)
    return predicate(page.url)


def urlsplit_path(url):
    from urllib.parse import urlsplit

    return urlsplit(url).path
