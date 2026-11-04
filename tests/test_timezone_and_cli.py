from __future__ import annotations

import datetime as dt
import sys
import types

import pytest
from conftest import SECRET, Base, Panel, User, make_site, seed
from fastapi import FastAPI
from sqlalchemy import DateTime, create_engine, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from fastapi_panel import AdminSite, ImproperlyConfigured, ModelAdmin, ModelAuth, verify_password
from fastapi_panel.__main__ import main
from fastapi_panel.log import AdminLog


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    naive: Mapped[dt.datetime] = mapped_column(DateTime)
    aware: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class EventAdmin(ModelAdmin):
    list_display = ("id", "naive")


def mount(site, engine):
    app = FastAPI()
    site.mount(app, engine=engine, auth=ModelAuth(User, superuser_field="is_superuser"), secret_key=SECRET)
    return Panel(TestClient(app), engine)


def panel_for(engine, **site_options):
    site = AdminSite(title="T", **site_options)
    site.register(Event, EventAdmin)
    panel = mount(site, engine)
    panel.login()
    return panel


@pytest.fixture
def tz_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
        session.add(Event(naive=dt.datetime(2026, 1, 1, 22, 0), aware=dt.datetime(2026, 1, 1, 22, 0)))
        session.commit()
    return engine


def test_unknown_timezone_fails_at_startup():
    with pytest.raises(ImproperlyConfigured, match="Mars/Olympus"):
        AdminSite(timezone="Mars/Olympus")


def test_times_are_shown_in_the_site_timezone(tz_engine):
    panel = panel_for(tz_engine, timezone="Asia/Baku")  # UTC+4
    assert "2026-01-02 02:00" in panel.get("/event").text
    form = panel.get("/event/1/change").text
    assert 'value="2026-01-02T02:00:00"' in form
    assert "In Asia/Baku." in form


def test_times_typed_in_the_form_are_stored_in_utc(tz_engine):
    panel = panel_for(tz_engine, timezone="Asia/Baku")
    page = panel.post("/event/1/change", {"naive": "2026-03-01T10:00:00", "aware": "2026-03-01T10:00:00", "_save": "1"})
    assert page.status_code in (200, 303)
    with Session(tz_engine) as session:
        event = session.get(Event, 1)
        assert event.naive == dt.datetime(2026, 3, 1, 6, 0)
        assert event.aware.replace(tzinfo=None) == dt.datetime(2026, 3, 1, 6, 0)


def test_saving_without_changes_keeps_the_stored_time(tz_engine):
    panel = panel_for(tz_engine, timezone="America/New_York")
    panel.post("/event/1/change", {"naive": "2026-01-01T17:00:00", "aware": "2026-01-01T17:00:00", "_save": "1"})
    with Session(tz_engine) as session:
        assert session.get(Event, 1).naive == dt.datetime(2026, 1, 1, 22, 0)


def test_naive_columns_can_be_left_alone(tz_engine):
    panel = panel_for(tz_engine, timezone="Asia/Baku", naive_utc=False)
    assert 'value="2026-01-01T22:00:00"' in panel.get("/event/1/change").text
    panel.post("/event/1/change", {"naive": "2026-03-01T10:00:00", "aware": "2026-03-01T10:00:00", "_save": "1"})
    with Session(tz_engine) as session:
        event = session.get(Event, 1)
        assert event.naive == dt.datetime(2026, 3, 1, 10, 0)
        assert event.aware.replace(tzinfo=None) == dt.datetime(2026, 3, 1, 6, 0)


def test_history_uses_the_site_timezone(tz_engine):
    panel = panel_for(tz_engine, timezone="Asia/Baku")
    panel.post("/event/1/change", {"naive": "2026-01-02T02:00:00", "aware": "2026-01-02T02:00:00", "_save": "1"})
    with Session(tz_engine) as session:
        entry = session.scalars(select(AdminLog)).first()
        entry.created_at = dt.datetime(2026, 5, 1, 23, 30)
        session.commit()
    page = panel.get("/event/1/history").text
    assert "When (Asia/Baku)" in page
    assert "2026-05-02 03:30:00" in page


def test_utc_site_shows_stored_values(tz_engine):
    panel = panel_for(tz_engine)
    assert "2026-01-01 22:00" in panel.get("/event").text
    assert "In UTC." not in panel.get("/item/add").text


@pytest.fixture
def mounted(tz_engine, monkeypatch):
    site = make_site()
    mount(site, tz_engine)
    module = types.ModuleType("fake_main")
    module.panel = site
    monkeypatch.setitem(sys.modules, "fake_main", module)
    monkeypatch.setenv("FASTAPI_PANEL_PASSWORD", "long enough password")
    return tz_engine


def run_cli(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["fastapi_panel", *args])
    return main()


def test_createuser_adds_a_superuser_that_can_log_in(mounted, monkeypatch):
    assert run_cli(monkeypatch, "createuser", "fake_main", "--username", "newboss") == 0
    with Session(mounted) as session:
        user = session.scalars(select(User).where(User.username == "newboss")).one()
        assert user.is_superuser and user.is_staff and user.is_active
        assert verify_password("long enough password", user.password)


def test_createuser_can_make_a_regular_user(mounted, monkeypatch):
    run_cli(monkeypatch, "createuser", "fake_main:panel", "--username", "plain", "--no-superuser")
    with Session(mounted) as session:
        assert not session.scalars(select(User).where(User.username == "plain")).one().is_superuser


def test_createuser_rejects_short_passwords_and_duplicates(mounted, monkeypatch):
    monkeypatch.setenv("FASTAPI_PANEL_PASSWORD", "short")
    with pytest.raises(SystemExit, match="at least 8"):
        run_cli(monkeypatch, "createuser", "fake_main", "--username", "x")
    monkeypatch.setenv("FASTAPI_PANEL_PASSWORD", "long enough password")
    with pytest.raises(SystemExit, match="Couldn't create"):
        run_cli(monkeypatch, "createuser", "fake_main", "--username", "root")


def test_createuser_explains_a_bad_target(mounted, monkeypatch):
    with pytest.raises(SystemExit, match="Couldn't import"):
        run_cli(monkeypatch, "createuser", "does_not_exist_anywhere", "--username", "x")
