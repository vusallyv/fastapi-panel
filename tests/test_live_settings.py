from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

import pytest
from conftest import PASSWORD_HASH, SECRET, Base, Panel, User, make_site
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from fastapi_panel import AdminLog, AdminSite, DecimalField, ImproperlyConfigured, ModelAuth, Setting
from fastapi_panel.log import LiveSetting


def definitions():
    return {
        "fee": Setting(Decimal("4.99"), group="Checkout", help_text="Per order.", field=DecimalField(min_value=0)),
        "closed": Setting(False, label="Shop closed"),
        "banner": Setting("hello"),
        "limit": Setting(10),
        "launch": Setting(dt.date(2026, 1, 1)),
        "options": Setting({"a": 1}),
    }


def build(engine):
    site = make_site()
    live = site.live_settings(**definitions())
    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key=SECRET)
    return live, app


@pytest.fixture
def setup(engine):
    live, app = build(engine)
    panel = Panel(TestClient(app), engine)
    panel.login()
    return live, app, panel


def form(**changes):
    values = {
        "fee": "4.99",
        "banner": "hello",
        "limit": "10",
        "launch": "2026-01-01",
        "options": '{"a": 1}',
    }
    return {**values, **changes}


def test_defaults_before_anything_is_saved(engine):
    live, _ = build(engine)
    assert (
        live.get("fee") == Decimal("4.99") and live.get("closed") is False and live.get("launch") == dt.date(2026, 1, 1)
    )


def test_page_shows_groups_help_and_defaults(setup):
    page = setup[2].get("/live-settings").text
    assert "Checkout" in page and "General" in page and "Shop closed" in page
    assert "Per order. Default: 4.99." in page


def test_saving_changes_every_type_and_applies_at_once(setup):
    live, _, panel = setup
    response = panel.post(
        "/live-settings",
        form(fee="5.50", closed="on", banner="sale", limit="3", launch="2027-02-03", options='{"b": 2}'),
    )
    assert response.status_code == 303
    assert live.get("fee") == Decimal("5.50") and isinstance(live.get("fee"), Decimal)
    assert live.get("closed") is True and live.get("banner") == "sale" and live.get("limit") == 3
    assert live.get("launch") == dt.date(2027, 2, 3) and live.get("options") == {"b": 2}
    assert 'value="5.50"' in panel.get("/live-settings").text


def test_returning_to_the_default_removes_the_row(setup, engine):
    live, _, panel = setup
    panel.post("/live-settings", form(limit="3"))
    panel.post("/live-settings", form())
    assert live.get("limit") == 10
    with Session(engine) as session:
        assert session.scalars(select(LiveSetting)).all() == []


def test_blank_number_means_default(setup):
    live, _, panel = setup
    panel.post("/live-settings", form(limit="3"))
    panel.post("/live-settings", form(limit=""))
    assert live.get("limit") == 10


def test_invalid_values_save_nothing(setup):
    live, _, panel = setup
    page = panel.post("/live-settings", form(fee="-1", limit="3"))
    assert page.status_code == 200 and "field-error" in page.text
    assert live.get("limit") == 10 and live.get("fee") == Decimal("4.99")


def test_changes_are_logged_with_old_and_new_values(setup, engine):
    _, _, panel = setup
    panel.post("/live-settings", form(fee="5.50", closed="on"))
    with Session(engine) as session:
        entries = session.scalars(select(AdminLog).where(AdminLog.model == "live-settings")).all()
    messages = {entry.object_id: entry.message for entry in entries}
    assert (
        messages["fee"] == "Fee: 4.99 → 5.50" and messages["closed"] == "Shop closed: No → Yes" and len(messages) == 2
    )
    activity = panel.get("/activity").text
    assert "Fee: 4.99 → 5.50" in activity and 'href="/admin/live-settings"' in activity
    assert "/live-settings/fee/change" not in activity


def test_permission_is_required(setup, engine):
    _, app, _ = setup
    with Session(engine) as session:
        session.add_all(
            [
                User(username="setter", password=PASSWORD_HASH, permissions=["settings.change"]),
                User(username="auditor", password=PASSWORD_HASH, permissions=["activity.view"]),
            ]
        )
        session.commit()
    viewer = Panel(TestClient(app), engine)
    viewer.login("viewer")
    assert viewer.get("/live-settings").status_code == 403
    assert viewer.post("/live-settings", form(limit="3")).status_code == 403
    assert "live-settings" not in viewer.get("/").text
    setter = Panel(TestClient(app), engine)
    setter.login("setter")
    assert setter.post("/live-settings", form(limit="3")).status_code == 303
    auditor = Panel(TestClient(app), engine)
    auditor.login("auditor")
    assert "live-settings" not in auditor.get("/activity").text


def test_a_value_that_no_longer_fits_falls_back_to_the_default(setup, engine):
    live, _, panel = setup
    with Session(engine) as session:
        session.add(LiveSetting(name="limit", value='"not a number"'))
        session.add(LiveSetting(name="launch", value="{broken"))
        session.commit()
    assert live.get("limit") == 10 and live.get("launch") == dt.date(2026, 1, 1)
    assert panel.get("/live-settings").status_code == 200


def test_misuse_fails_clearly(engine):
    site = AdminSite()
    with pytest.raises(ImproperlyConfigured, match="secret"):
        site.live_settings(api_key=Setting("x"))
    with pytest.raises(ImproperlyConfigured, match="can't be"):
        Setting(None)
    live = site.live_settings(banner=Setting("x"))
    with pytest.raises(RuntimeError, match="mount"):
        live.get("banner")
    with pytest.raises(ImproperlyConfigured, match="once"):
        site.live_settings(other=Setting("x"))
    live, _ = build(engine)
    with pytest.raises(KeyError):
        live.get("missing")


def test_no_settings_no_page(panel):
    assert panel.get("/live-settings").status_code == 404


def test_async_engine_reads_with_aget(tmp_path):
    asyncio.run(_async_reads(tmp_path))


async def _async_reads(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/live.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    site = AdminSite()
    live = site.live_settings(limit=Setting(10))
    auth = ModelAuth(User, superuser_field="is_superuser")
    site.mount(FastAPI(), engine=engine, auth=auth, secret_key=SECRET)
    assert await live.aget("limit") == 10
    async with AsyncSession(engine) as session:
        session.add(LiveSetting(name="limit", value="7"))
        await session.commit()
    assert await live.aget("limit") == 7
    with pytest.raises(RuntimeError, match="aget"):
        live.get("limit")
    await engine.dispose()
