#!/usr/bin/env python3
"""Check site HTTP responses, real BrowserGym login, and the configured LLM."""

import argparse
import concurrent.futures
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skilltracer.config import DEFAULT_MODEL, SITE_URLS


def check_http(site):
    url = SITE_URLS[site]
    if site == "shopping_admin":
        url = url.removesuffix("/admin") + "/admin"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return {
                "site": site,
                "url": url,
                "ok": response.status == 200,
                "status": response.status,
                "final_url": response.url,
            }
    except Exception as exc:
        return {"site": site, "url": url, "ok": False, "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sites",
        nargs="+",
        choices=SITE_URLS,
        default=["shopping", "shopping_admin", "reddit", "gitlab", "map"],
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Verify BrowserGym observations and benchmark account login",
    )
    parser.add_argument(
        "--llm", action="store_true", help="Make one small paid request to the configured model"
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"model": DEFAULT_MODEL, "http": []}
    with concurrent.futures.ThreadPoolExecutor() as pool:
        for result in pool.map(check_http, args.sites):
            report["http"].append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    ok = all(result["ok"] for result in report["http"])
    if args.browser:
        from skilltracer.browser.executor import BrowsergymExecutor
        from skilltracer.browser.login import login_site_with_env

        report["browser"] = []
        for site in args.sites:
            executor = None
            try:
                executor = BrowsergymExecutor(SITE_URLS[site], timeout=60000)
                login = (
                    login_site_with_env(executor, site)
                    if site in {"shopping", "shopping_admin", "reddit", "gitlab"}
                    else True
                )
                state = executor.get_page_state()
                page = executor.env.unwrapped.page
                authenticated = {
                    "shopping": lambda: page.get_by_text("Sign Out", exact=True).count() > 0,
                    "shopping_admin": lambda: (
                        "/admin/" in page.url
                        and page.get_by_role("button", name="Sign in", exact=True).count() == 0
                    ),
                    "reddit": lambda: (
                        page.get_by_role("button", name="Log out", exact=True).count() > 0
                        or page.get_by_text("MarvelsGrantMan136", exact=False).count() > 0
                    ),
                    "gitlab": lambda: (
                        "/users/sign_in" not in page.url
                        and page.locator(
                            "img.header-user-avatar, [data-testid='user-menu-toggle'], a[href='/users/sign_out']"
                        ).count()
                        > 0
                    ),
                }
                verified = authenticated[site]() if site in authenticated else True
                result = {
                    "site": site,
                    "ok": bool(login and verified and state["axtree_object"]),
                    "url": state["url"],
                    "axtree_chars": len(state["axtree_object"]),
                }
            except Exception as exc:
                result = {"site": site, "ok": False, "error": str(exc)}
            finally:
                if executor is not None:
                    executor.close()
            report["browser"].append(result)
            ok = ok and result["ok"]
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.llm:
        from skilltracer.llm import LLMConfig, build_llm

        try:
            reply = build_llm(LLMConfig(max_output_tokens=512))(
                "Reply with exactly: SKILLTRACER_OK"
            )
            result = {
                "model": DEFAULT_MODEL,
                "ok": reply.strip() == "SKILLTRACER_OK",
                "reply": reply,
            }
        except Exception as exc:
            result = {"model": DEFAULT_MODEL, "ok": False, "error": str(exc)}
        report["llm"] = result
        ok = ok and result["ok"]
        print(json.dumps(result, ensure_ascii=False), flush=True)
    report["ok"] = ok
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
