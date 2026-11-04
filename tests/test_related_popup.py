from __future__ import annotations

import re

from conftest import Owner
from sqlalchemy import select
from sqlalchemy.orm import Session


def add_buttons(html):
    return re.findall(r'data-related-add="([^"]+)"', html)


def test_foreign_keys_and_many_to_many_get_an_add_button(panel):
    urls = add_buttons(panel.get("/item/add").text)
    assert "/admin/owner/add?_popup=1" in [url.replace("&amp;", "&") for url in urls]
    assert "/admin/tag/add?_popup=1" in urls


def test_no_button_without_add_permission(anon):
    anon.login("editor")  # item.* only, no owner.add
    assert add_buttons(anon.get("/item/add").text) == []


def test_popup_form_has_no_chrome_and_only_save_and_cancel(panel):
    page = panel.get("/owner/add?_popup=1").text
    assert 'id="sidebar"' not in page and 'class="topbar"' not in page
    assert "data-popup-cancel" in page and 'name="_save"' in page
    assert 'name="_continue"' not in page and 'name="_addanother"' not in page


def test_no_popup_inside_a_popup(panel):
    assert add_buttons(panel.get("/item/add?_popup=1").text) == []


def test_saving_hands_the_new_row_back_without_a_message(panel, engine):
    response = panel.post("/owner/add?_popup=1", {"name": 'Eve "<b>"', "_save": "1"})
    assert response.status_code == 200
    assert "data-popup-result" in response.text
    assert "<b>" not in response.text
    with Session(engine) as session:
        owner = session.scalars(select(Owner).where(Owner.name.like("Eve%"))).one()
    assert f'data-id="{owner.id}"' in response.text
    assert "was added successfully" not in panel.get("/owner").text


def test_validation_errors_stay_in_the_popup(panel):
    page = panel.post("/owner/add?_popup=1", {"name": "", "_save": "1"})
    assert page.status_code == 200 and "This field is required" in page.text and 'id="sidebar"' not in page.text


def test_only_the_popup_add_form_may_be_framed(panel):
    popup = panel.get("/owner/add?_popup=1").headers
    assert "frame-ancestors 'self'" in popup["content-security-policy"]
    assert popup["x-frame-options"] == "SAMEORIGIN"

    for url in ("/owner/add", "/owner", "/owner/1/change?_popup=1", "/owner?_popup=1", "/?_popup=1"):
        headers = panel.get(url).headers
        assert "frame-ancestors 'none'" in headers["content-security-policy"], url
        assert headers["x-frame-options"] == "DENY", url


def test_popup_add_still_needs_permission(anon):
    anon.login("editor")
    assert anon.get("/owner/add?_popup=1").status_code == 403
    assert anon.post("/owner/add?_popup=1", {"name": "x", "_save": "1"}).status_code == 403
