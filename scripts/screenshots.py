"""Drive the example shop in a real browser, fail on any console or CSP error, and save docs/screenshots.

Needs the example running on :8765 and Chrome installed:
    uv run --with playwright python scripts/screenshots.py
"""

import struct
import sys
import tempfile
import zlib
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765/admin"
OUT = Path(__file__).parent.parent / "docs" / "screenshots"


def sample_png(path: Path, size: int = 96) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(
        b"\0" + bytes(value for x in range(size) for value in (12 + x, 107 + y // 2, 88 + (x + y) // 2))
        for y in range(size)
    )
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    picture = Path(tempfile.mkdtemp()) / "lamp.png"
    sample_png(picture)

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

        page.goto(f"{BASE}/order/1/delete")
        shot("delete")

        # An image upload, with its preview and the thumbnail it leaves on the list.
        page.goto(f"{BASE}/product/1/change")
        page.set_input_files("#id_image", str(picture))
        page.wait_for_selector("[data-file-preview]:not([hidden])")
        shot("product-form", full_page=True)
        page.click("button[name=_continue]")
        page.wait_for_selector(".message-success")
        page.goto(f"{BASE}/product?o=name")
        thumbnail = page.locator("img.thumb").first
        thumbnail.wait_for()
        page.wait_for_function("document.querySelector('img.thumb').naturalWidth > 0")

        # The + button on a dropdown, a two-column picker and an autocomplete.
        page.goto(f"{BASE}/product/add")
        page.locator("[data-related-add*='/category/add']").click()
        dialog = page.frame_locator("dialog iframe")
        dialog.locator("#id_name").fill("Lighting")
        assert dialog.locator("#id_slug").input_value() == "lighting"
        shot("related-popup")
        dialog.locator("button[name=_save]").click()
        page.wait_for_selector("dialog", state="detached")
        chosen = page.locator("[data-autocomplete][data-name=category] .chip").inner_text()
        assert "Lighting" in chosen, chosen

        page.locator("details.fieldset summary").click()
        page.locator("[data-related-add*='/tag/add']").click()
        page.frame_locator("dialog iframe").locator("#id_name").fill("Handmade")
        page.frame_locator("dialog iframe").locator("button[name=_save]").click()
        page.wait_for_selector("dialog", state="detached")
        assert "Handmade" in page.locator(".dual-column").nth(1).inner_text(), "the new tag should be chosen"

        page.locator("[data-related-add*='/category/add']").click()
        page.frame_locator("dialog iframe").locator("[data-popup-cancel]").click()
        page.wait_for_selector("dialog", state="detached")

        page.goto(f"{BASE}/order/add")
        page.locator("[data-related-add*='/customer/add']").first.click()
        dialog = page.frame_locator("dialog iframe")
        dialog.locator("#id_name").fill("Rina Aoki")
        dialog.locator("#id_email").fill("rina@example.com")
        dialog.locator("#id_country").fill("JP")
        dialog.locator("button[name=_save]").click()
        page.wait_for_selector("dialog", state="detached")
        assert "Rina Aoki" in page.locator(".chip").first.inner_text()

        # CSV import: a file with a mistake is refused, then the fixed file goes in.
        page.goto(f"{BASE}/product")
        page.click("text=Import")
        bad = Path(picture).with_name("products-bad.csv")
        bad.write_text("Name,Slug,Category,Price\nOak Stool,oak-stool,Home,not-a-price\n", encoding="utf-8")
        page.set_input_files("#id_file", str(bad))
        page.click("button:has-text('Check file')")
        page.wait_for_selector(".tag-error")
        assert page.locator("button[name=_confirm]").count() == 0
        good = Path(picture).with_name("products.csv")
        good.write_text(
            "Name,Slug,Category,Price,Stock,Is published\n"
            "Oak Stool,oak-stool,Home,89.00,12,True\nLinen Shade,linen-shade,Lighting,45.50,4,False\n"
            "Clay Vase,clay-vase,Home,32.00,20,True\n",
            encoding="utf-8",
        )
        page.set_input_files("#id_file", str(good))
        page.click("button:has-text('Check file')")
        page.wait_for_selector("button[name=_confirm]")
        shot("import-preview")
        page.click("button[name=_confirm]")
        page.wait_for_selector(".message-success")
        assert page.locator("text=Oak Stool").count() >= 1 or "3 added" in page.inner_text(".message-success")

        # Soft delete: gone from the list, listed under Deleted, then back.
        page.goto(f"{BASE}/product?q=oak")
        page.locator("a.row-link").first.click()
        page.click("a.button-danger")
        assert "restore" in page.inner_text(".card")
        page.click("button.button-danger")
        page.wait_for_selector(".message-success")
        page.goto(f"{BASE}/product?q=linen")
        page.locator("a.row-link").first.click()
        page.click("a.button-danger")
        page.click("button.button-danger")
        page.goto(f"{BASE}/product")
        page.click("a:has-text('Deleted')")
        page.wait_for_selector("button[name=_restore]")
        assert page.locator("button[name=_restore]").count() == 2
        shot("deleted")
        page.locator("button[name=_restore]").first.click()
        page.wait_for_selector(".message-success")
        assert page.locator("button[name=_restore]").count() == 1

        # The rich text editor: formatting and a link go through the toolbar and survive a save.
        page.goto(f"{BASE}/product/2/change")
        page.locator("details.fieldset summary").click()
        editor = page.locator(".rich-editor")
        editor.click()
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.type("Made to last for years.")
        page.keyboard.press("ControlOrMeta+A")
        page.click("button[aria-label=Bold]")
        page.click("button[aria-label=Link]")
        page.fill(".rich-link input", "example.com/care")
        page.keyboard.press("Enter")
        page.wait_for_selector(".rich-editor a")
        shot("rich-text", full_page=True)
        page.click("button[name=_continue]")
        page.wait_for_selector(".message-success")
        saved = page.locator(".rich-editor").inner_html()
        assert "<strong>" in saved and 'href="https://example.com/care"' in saved, saved

        # Old and new values in the history, for the fields listed in log_values.
        page.fill("#id_price", "12.50")
        page.click("button[name=_continue]")
        page.wait_for_selector(".message-success")
        page.goto(f"{BASE}/product/2/history")
        assert "→ 12.50" in page.inner_text(".card")
        shot("history")

        # A saved filter: keep this view, then open it again from the list.
        page.goto(f"{BASE}/product?is_published=1&stock_level=low")
        page.locator(".save-filter summary").click()
        page.fill(".save-filter input[name=name]", "Low stock, live")
        page.click(".save-filter button")
        page.wait_for_selector(".message-success")
        page.goto(f"{BASE}/product")
        page.locator(".saved-filters").get_by_text("Low stock, live").click()
        page.wait_for_url("**stock_level=low**")
        assert page.locator(".chip-active").count() == 1
        shot("saved-filters")

        # Everyone's activity, with filters.
        page.goto(f"{BASE}/activity")
        shot("activity")
        page.select_option("#activity-action", "delete")
        page.click(".activity-filters button")
        page.wait_for_url("**action=delete**")
        assert page.locator(".tag-delete").count() >= 1 and page.locator("tbody .tag-add").count() == 0

        # Forgotten password, from a browser that isn't logged in.
        visitor = browser.new_context(locale="en-US").new_page()
        visitor.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        visitor.goto(f"{BASE}/login")
        visitor.click("text=Forgot your password?")
        visitor.fill("#username", "admin")
        visitor.click("button[type=submit]")
        visitor.wait_for_selector(".message-success")
        visitor.close()

        page.goto(f"{BASE}/")
        shot("dashboard")

        page.fill("[data-nav-filter]", "add")
        assert page.locator("[data-nav-item]:visible").count() == 0, "the Add links shouldn't match the model filter"
        page.fill("[data-nav-filter]", "prod")
        assert page.locator("[data-nav-item]:visible").count() == 1
        page.fill("[data-nav-filter]", "")

        access = page.locator('[data-nav-group="Access"]')

        def is_open() -> bool:
            return access.evaluate("group => group.open")

        access.locator("summary").click()
        assert not is_open(), "clicking a group title should collapse it"
        page.reload()
        assert not is_open(), "a collapsed group should stay collapsed"
        page.fill("[data-nav-filter]", "staff")
        assert is_open() and access.locator("[data-nav-item]:visible").count() == 1, "the filter should reveal matches"
        page.fill("[data-nav-filter]", "")
        assert not is_open(), "clearing the filter should restore the group"
        page.goto(f"{BASE}/staff")
        assert is_open(), "the current page's group should always be open"

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
