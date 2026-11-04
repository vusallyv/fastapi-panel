from __future__ import annotations

import re
import sys
import time
import types

import pytest
from conftest import PASSWORD, SECRET, Panel, User, make_site
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from fastapi_panel import AdminSite, ModelAuth, StaticAuth, hash_password, verify_password
from fastapi_panel.__main__ import main

NEW = "a brand new password"


def mount(engine, send, **options):
    site = make_site()
    app = FastAPI()
    auth = options.pop("auth", ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions"))
    site.mount(
        app,
        engine=engine,
        auth=auth,
        secret_key=SECRET,
        password_reset=send,
        public_url="https://panel.test/admin",
        **options,
    )
    return site, Panel(TestClient(app), engine)


@pytest.fixture
def outbox():
    return []


@pytest.fixture
def setup(engine, outbox):
    return mount(engine, lambda user, url: outbox.append((user.username, url)))


@pytest.fixture
def reset(setup):
    return setup[1]


def link_path(url):
    return url.removeprefix("https://panel.test/admin")


def password_of(engine, username="root"):
    with Session(engine) as session:
        return session.scalars(select(User.password).where(User.username == username)).one()


def test_login_links_to_it_only_when_configured(reset, anon):
    assert "/admin/forgot-password" in reset.get("/login").text
    assert "/admin/forgot-password" not in anon.get("/login").text
    assert anon.get("/forgot-password").status_code == 404


def test_needs_a_public_url(engine):
    with pytest.raises(ValueError, match="public_url"):
        make_site().mount(FastAPI(), engine=engine, auth=ModelAuth(User), secret_key=SECRET, password_reset=print)


def test_full_flow(reset, outbox, engine):
    page = reset.post("/forgot-password", {"username": "root"})
    assert "a reset link is on its way" in page.text
    assert outbox[0][0] == "root" and outbox[0][1].startswith("https://panel.test/admin/reset-password/")
    path = link_path(outbox[0][1])

    assert reset.get(path).status_code == 200
    response = reset.post(path, {"new": NEW, "confirm": NEW})
    assert response.status_code == 303
    assert verify_password(NEW, password_of(engine))
    assert "Log in with the new one" in reset.get("/login").text
    assert reset.login("root", NEW).status_code == 303


def test_host_header_cant_change_the_link(reset, outbox):
    token = reset.get("/forgot-password").text
    assert token
    reset.client.post(
        "/admin/forgot-password",
        data={"username": "root", "csrf_token": re.search(r'csrf_token" value="([^"]+)', token).group(1)},
        headers={"host": "evil.example", "origin": "http://evil.example"},
    )
    assert outbox and all(url.startswith("https://panel.test/admin/") for _, url in outbox)


def test_same_answer_for_unknown_and_blocked_users(reset, outbox):
    answers = {reset.post("/forgot-password", {"username": name}).text for name in ("nobody", "customer", "gone")}
    assert len(answers) == 1 and "on its way" in answers.pop()
    assert outbox == []


def test_links_are_rate_limited_per_username(reset, outbox):
    for _ in range(6):
        reset.post("/forgot-password", {"username": "root"})
    assert len(outbox) == 3


def test_link_works_once(reset, outbox):
    reset.post("/forgot-password", {"username": "root"})
    path = link_path(outbox[0][1])
    reset.post(path, {"new": NEW, "confirm": NEW})
    assert reset.get(path).status_code == 400
    assert reset.post(path, {"new": "another password!", "confirm": "another password!"}).status_code == 400


def test_old_link_dies_when_the_password_changes_elsewhere(reset, outbox, engine):
    reset.post("/forgot-password", {"username": "root"})
    with Session(engine) as session:
        session.scalars(select(User).where(User.username == "root")).one().password = hash_password("changed elsewhere")
        session.commit()
    assert reset.get(link_path(outbox[0][1])).status_code == 400


def test_expired_and_forged_links_fail(setup, engine):
    site, panel = setup
    with Session(engine) as session:
        user = session.scalars(select(User).where(User.username == "root")).one()
        claims = {"uid": str(user.id), "hash": site.auth.session_hash(user)}
    assert (
        panel.get(f"/reset-password/{site.reset_signer.dumps({**claims, 'exp': time.time() - 1})}").status_code == 400
    )
    assert panel.get(f"/reset-password/{site.signer.dumps({**claims, 'exp': time.time() + 99})}").status_code == 400
    assert (
        panel.get(f"/reset-password/{site.file_signer.dumps({**claims, 'exp': time.time() + 99})}").status_code == 400
    )
    assert panel.get("/reset-password/garbage").status_code == 400


def test_deactivated_user_cant_use_a_link(reset, outbox, engine):
    reset.post("/forgot-password", {"username": "root"})
    with Session(engine) as session:
        session.scalars(select(User).where(User.username == "root")).one().is_active = False
        session.commit()
    assert reset.get(link_path(outbox[0][1])).status_code == 400


def test_new_password_is_checked(reset, outbox, engine):
    before = password_of(engine)
    reset.post("/forgot-password", {"username": "root"})
    path = link_path(outbox[0][1])
    assert "at least 8" in reset.post(path, {"new": "short", "confirm": "short"}).text
    assert "don&#39;t match" in reset.post(path, {"new": NEW, "confirm": "different password"}).text
    assert password_of(engine) == before


def test_reset_ends_open_sessions(setup, outbox, engine):
    _, reset = setup
    other = Panel(TestClient(reset.client.app), engine)
    other.login("root", PASSWORD)
    assert other.get("/item").status_code == 200
    reset.post("/forgot-password", {"username": "root"})
    path = link_path(outbox[0][1])
    reset.post(path, {"new": NEW, "confirm": NEW})
    assert other.get("/item", follow_redirects=False).status_code == 303


def test_needs_csrf(reset, outbox):
    assert reset.post("/forgot-password", {"username": "root"}, csrf=False).status_code == 403
    assert outbox == []


def test_a_failing_mailer_doesnt_show(engine):
    def broken(user, url):
        raise RuntimeError("smtp is down")

    _, panel = mount(engine, broken)
    assert "on its way" in panel.post("/forgot-password", {"username": "root"}).text


def test_async_mailer(engine, outbox):
    async def send(user, url):
        outbox.append(url)

    _, panel = mount(engine, send)
    panel.post("/forgot-password", {"username": "root"})
    assert len(outbox) == 1


def test_static_auth_has_no_reset(engine):
    site = AdminSite()
    app = FastAPI()
    auth = StaticAuth({"a": hash_password(PASSWORD)})
    site.mount(app, engine=engine, auth=auth, secret_key=SECRET, password_reset=print, public_url="https://x.test")
    panel = Panel(TestClient(app), engine)
    assert panel.get("/forgot-password").status_code == 404


@pytest.fixture
def cli(engine, monkeypatch):
    site = make_site()
    site.mount(FastAPI(), engine=engine, auth=ModelAuth(User, superuser_field="is_superuser"), secret_key=SECRET)
    module = types.ModuleType("fake_reset_main")
    module.panel = site
    monkeypatch.setitem(sys.modules, "fake_reset_main", module)
    monkeypatch.setenv("FASTAPI_PANEL_PASSWORD", NEW)


def test_changepassword_command(cli, engine, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x", "changepassword", "fake_reset_main", "--username", "root"])
    assert main() == 0
    assert verify_password(NEW, password_of(engine))


def test_changepassword_unknown_user(cli, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x", "changepassword", "fake_reset_main", "--username", "ghost"])
    with pytest.raises(SystemExit, match="no user called ghost"):
        main()
