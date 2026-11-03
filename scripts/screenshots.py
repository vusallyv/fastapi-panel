"""Drive the example shop in a real browser, fail on any console or CSP error, and save docs/screenshots.

Needs the example running on :8765 and Chrome installed:
    uv run --with playwright python scripts/screenshots.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765/admin"
OUT = Path(__file__).parent.parent / "docs" / "screenshots"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", args=["--lang=en-US"])
        context = browser.new_context(viewport={"width": 1360, "height": 860}, color_scheme="light", locale="en-US")
        page = context.new_page()
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))

        def shot(name: str, **kwargs) -> None:
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=OUT / f"{name}.png", **kwargs)

        page.goto(f"{BASE}/")
        assert page.url.endswith("/login?next=%2Fadmin%2F"), page.url
        shot("login")
        page.fill("#username", "admin")
        page.fill("#password", "admin")
        page.click("button[type=submit]")
        page.wait_for_selector(".dashboard")

        # Leave some history behind so the dashboard isn't empty.
        page.goto(f"{BASE}/category/add")
        page.fill("#id_name", "Garden Tools")
        assert page.input_value("#id_slug") == "garden-tools", "slug should prefill from the name"
        page.click("button[name=_save]")
        page.wait_for_selector(".message-success")

        page.goto(f"{BASE}/product")
        page.check("[data-select-all]")
        assert "20 of 20" in page.inner_text("[data-counter]")
        page.click("[data-select-across]")
        assert page.input_value("[data-across-input]") == "1"
        shot("changelist")
        page.uncheck("[data-select-all]")

        page.select_option("[data-action-select]", "publish")
        page.locator("[data-row-check]").first.check()
        page.click("button[name=_run_action]")
        page.wait_for_selector(".message-success")

        page.goto(f"{BASE}/product?q=lamp")
        assert page.locator("[data-row-check]").count() == 1

        page.goto(f"{BASE}/product/1/change")
        page.locator("details.fieldset summary").click()
        assert page.locator(".dual-column select").count() == 2, "dual select should be built"
        page.locator(".dual-column select").first.select_option(index=0)
        page.click(".dual-buttons button:first-child")
        shot("product-form", full_page=True)
        page.click("button[name=_continue]")
        page.wait_for_selector(".message-success")

        page.goto(f"{BASE}/order/1/change")
        before = page.locator("[data-inline-row]").count()
        page.click("[data-inline-add]")
        assert page.locator("[data-inline-row]").count() == before + 1
        row = page.locator("[data-inline-row]").last
        row.locator("[data-search]").click()
        row.locator("[data-search]").fill("lamp")
        row.locator(".autocomplete-results button").first.wait_for()
        row.locator(".autocomplete-results button").first.click()
        assert row.locator(".chip").count() == 1, "autocomplete should add a chip"
        row.locator("input[name$=unit_price]").fill("12.50")
        shot("order-inline", full_page=True)
        page.locator("button[name=_continue]").first.click()
        page.wait_for_selector(".message-success")
        assert page.locator("[data-inline-row]").count() >= before + 1

        page.goto(f"{BASE}/order/1/history")
        shot("history")

        page.goto(f"{BASE}/order/1/delete")
        shot("delete")

        page.goto(f"{BASE}/")
        shot("dashboard")

        page.click("[data-toggle-theme]")
        page.goto(f"{BASE}/product")
        shot("changelist-dark")
        page.click("[data-toggle-theme]")

        phone = browser.new_context(
            viewport={"width": 390, "height": 800}, storage_state=context.storage_state(), locale="en-US"
        )
        mobile = phone.new_page()
        mobile.goto(f"{BASE}/product")
        width = mobile.evaluate("document.documentElement.scrollWidth")
        if width > 390:
            problems.append(f"horizontal page scroll on a phone: {width}px")
        mobile.screenshot(path=OUT / "mobile.png")
        mobile.click("[data-toggle-sidebar]")
        assert mobile.locator("#sidebar.open").count() == 1
        browser.close()

    for problem in problems:
        print("PROBLEM:", problem)
    print("done" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
