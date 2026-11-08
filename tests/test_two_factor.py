from __future__ import annotations

import base64
import re
import sys
import time
import types

import pytest
from conftest import SECRET, Panel, User, build_client, make_site
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from fastapi_panel import ModelAuth, two_factor
from fastapi_panel.__main__ import main
from fastapi_panel.log import TwoFactorDevice


def test_codes_match_rfc_6238():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert two_factor.code_at(secret, 59 // 30) == "287082"
    assert two_factor.code_at(secret, 1111111109 // 30) == "081804"


def test_a_code_is_refused_once_its_step_was_used():
    secret = two_factor.new_secret()
    now = 1_000_000.0
    step = int(now // 30)
    code = two_factor.code_at(secret, step)
    assert two_factor.matching_step(secret, code, last_step=0, now=now) == step
    assert two_factor.matching_step(secret, code, last_step=step, now=now) is None
    assert two_factor.matching_step(secret, two_factor.code_at(secret, step - 5), last_step=0, now=now) is None


@pytest.fixture
def mfa(engine):
    _, app = build_client(engine, two_factor=True)
    return Panel(TestClient(app), engine)


def setup_secret(page_html: str) -> str:
    return re.search(r'class="two-factor-key">([^<]+)<', page_html).group(1).replace(" ", "")


def current_code(secret: str) -> str:
    return two_factor.code_at(secret, int(time.time() // 30))


def test_password_alone_does_not_log_in(mfa):
    response = mfa.login()
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/two-factor"
    assert mfa.get("/", follow_redirects=False).headers["location"].startswith("/admin/login")


def test_first_login_sets_up_then_later_logins_ask_for_a_code(mfa, engine):
    mfa.login()
    page = mfa.get("/two-factor")
    assert "Scan the code" in page.text and "data:image/svg+xml" in page.text
    secret = setup_secret(page.text)

    assert mfa.post("/two-factor", {"code": "000000"}).status_code == 400
    response = mfa.post("/two-factor", {"code": current_code(secret)})
    assert response.status_code == 303
    assert mfa.get("/", follow_redirects=False).status_code == 200
    with Session(engine) as session:
        assert session.get(TwoFactorDevice, "1").secret == secret

    mfa.post("/logout")
    mfa.login()
    page = mfa.get("/two-factor")
    assert "Scan the code" not in page.text
    # The code that set it up was used, so it can't log in again.
    assert mfa.post("/two-factor", {"code": current_code(secret)}).status_code == 400


def test_the_next_page_survives_the_code_step(mfa):
    mfa.post("/login?next=/admin/item", {"username": "root", "password": "correct horse battery"})
    secret = setup_secret(mfa.get("/two-factor").text)
    assert mfa.post("/two-factor", {"code": current_code(secret)}).headers["location"] == "/admin/item"


def test_wrong_codes_are_throttled(mfa):
    mfa.login()
    secret = setup_secret(mfa.get("/two-factor").text)
    for _ in range(5):
        mfa.post("/two-factor", {"code": "000000"})
    assert mfa.post("/two-factor", {"code": current_code(secret)}).status_code == 429


def test_without_a_pending_login_the_page_sends_you_back(mfa):
    assert mfa.get("/two-factor", follow_redirects=False).headers["location"] == "/admin/login"


def test_sessions_from_before_it_was_switched_on_must_log_in_again(engine):
    _, plain = build_client(engine)
    before = TestClient(plain)
    Panel(before, engine).login()
    assert before.get("/admin/", follow_redirects=False).status_code == 200
    _, protected = build_client(engine, two_factor=True)
    after = TestClient(protected, cookies=before.cookies)
    assert after.get("/admin/", follow_redirects=False).status_code == 303


def test_switched_off_the_page_does_not_exist(anon):
    assert anon.get("/two-factor").status_code == 404


def test_reset_command_removes_the_device(mfa, engine, monkeypatch):
    mfa.login()
    mfa.post("/two-factor", {"code": current_code(setup_secret(mfa.get("/two-factor").text))})
    site = make_site()
    site.mount(FastAPI(), engine=engine, auth=ModelAuth(User), secret_key=SECRET, two_factor=True)
    module = types.ModuleType("fake_two_factor_main")
    module.panel = site
    monkeypatch.setitem(sys.modules, "fake_two_factor_main", module)
    monkeypatch.setattr(sys, "argv", ["x", "reset-2fa", "fake_two_factor_main", "--username", "root"])
    assert main() == 0
    with Session(engine) as session:
        assert session.scalars(select(TwoFactorDevice)).first() is None
    monkeypatch.setattr(sys, "argv", ["x", "reset-2fa", "fake_two_factor_main", "--username", "ghost"])
    with pytest.raises(SystemExit, match="no user called ghost"):
        main()
