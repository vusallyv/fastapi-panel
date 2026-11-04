from __future__ import annotations

import pytest
from conftest import PASSWORD, PASSWORD_HASH, SECRET, Panel, User, make_site
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from fastapi_panel import ModelAdmin, ModelAuth, StaticAuth, verify_password
from fastapi_panel.security import hash_password

NEW = "a brand new password"


class UserAdmin(ModelAdmin):
    list_display = ("username",)
    exclude = ("permissions",)


class ExplicitAdmin(UserAdmin):
    fields = ("username", "password", "is_active", "is_staff", "is_superuser")


def build(engine, admin_class=UserAdmin, auth=None):
    site = make_site()
    site.register(User, admin_class)
    app = FastAPI()
    auth = auth or ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key=SECRET)
    return app


def login(app, engine, username="root"):
    panel = Panel(TestClient(app), engine)
    panel.login(username)
    return panel


def stored(engine, username):
    with Session(engine) as session:
        return session.scalar(select(User.password).where(User.username == username))


def pk(engine, username):
    with Session(engine) as session:
        return session.scalar(select(User.id).where(User.username == username))


@pytest.fixture
def app(engine):
    with Session(engine) as session:
        session.add(User(username="manager", password=PASSWORD_HASH, permissions=["user.*"]))
        session.commit()
    return build(engine)


@pytest.fixture
def root(app, engine):
    return login(app, engine)


def test_add_form_asks_for_a_password_and_stores_a_hash(root, app, engine):
    page = root.get("/user/add").text
    assert 'name="password"' in page and 'type="password"' in page
    data = {"username": "new", "password": NEW, "is_active": "on", "is_staff": "on"}
    assert root.post("/user/add", data).status_code == 303
    assert stored(engine, "new") != NEW and verify_password(NEW, stored(engine, "new"))
    assert Panel(TestClient(app), engine).login("new", NEW).status_code == 303


def test_password_is_required_and_checked_on_add(root, engine):
    page = root.post("/user/add", {"username": "new", "password": ""})
    assert "This field is required." in page.text
    page = root.post("/user/add", {"username": "new", "password": "short"})
    assert "at least 8" in page.text and 'value="short"' not in page.text
    assert stored(engine, "new") is None


def test_change_form_never_shows_the_hash_and_empty_keeps_it(root, engine):
    pk_viewer = pk(engine, "viewer")
    before = stored(engine, "viewer")
    page = root.get(f"/user/{pk_viewer}/change").text
    assert before not in page and "Leave empty" in page
    data = {"username": "viewer", "password": "", "is_active": "on", "is_staff": "on"}
    assert root.post(f"/user/{pk_viewer}/change", data).status_code == 303
    assert stored(engine, "viewer") == before


def test_superuser_can_reset_and_only_the_field_name_is_logged(root, engine):
    pk_viewer = pk(engine, "viewer")
    data = {"username": "viewer", "password": NEW, "is_active": "on", "is_staff": "on"}
    root.post(f"/user/{pk_viewer}/change", data)
    assert verify_password(NEW, stored(engine, "viewer"))
    history = root.get(f"/user/{pk_viewer}/history").text
    assert "password" in history.lower() and NEW not in history and stored(engine, "viewer") not in history


def test_non_superuser_cant_change_someone_elses_password(app, engine):
    manager = login(app, engine, "manager")
    pk_root = pk(engine, "root")
    before = stored(engine, "root")
    assert 'name="password"' not in manager.get(f"/user/{pk_root}/change").text
    manager.post(f"/user/{pk_root}/change", {"username": "root", "password": NEW, "is_active": "on", "is_staff": "on"})
    assert stored(engine, "root") == before
    assert 'name="password"' in manager.get("/user/add").text


def test_explicit_fields_get_the_safe_field_and_no_hash_for_others(engine):
    with Session(engine) as session:
        session.add(User(username="manager", password=PASSWORD_HASH, permissions=["user.*"]))
        session.commit()
    app = build(engine, ExplicitAdmin)
    root = login(app, engine)
    pk_viewer = pk(engine, "viewer")
    hashed = stored(engine, "viewer")
    assert hashed not in root.get(f"/user/{pk_viewer}/change").text
    manager = login(app, engine, "manager")
    page = manager.get(f"/user/{pk_viewer}/change").text
    assert hashed not in page and 'name="password"' not in page
    manager.post(
        f"/user/{pk_viewer}/change", {"username": "viewer", "password": NEW, "is_active": "on", "is_staff": "on"}
    )
    assert stored(engine, "viewer") == hashed


def test_view_only_user_never_sees_the_hash(engine):
    with Session(engine) as session:
        session.add(User(username="looker", password=PASSWORD_HASH, permissions=["user.view"]))
        session.commit()
    app = build(engine, ExplicitAdmin)
    looker = login(app, engine, "looker")
    page = looker.get(f"/user/{pk(engine, 'viewer')}/change").text
    assert stored(engine, "viewer") not in page


def test_other_models_with_a_password_column_are_untouched(root):
    assert "Leave empty" not in root.get("/owner/add").text


def test_static_auth_has_no_password_field(engine):
    app = build(engine, auth=StaticAuth({"root": hash_password(PASSWORD)}))
    panel = Panel(TestClient(app), engine)
    panel.post("/login", {"username": "root", "password": PASSWORD})
    page = panel.get("/user/add")
    assert page.status_code == 200 and 'type="password"' not in page.text
