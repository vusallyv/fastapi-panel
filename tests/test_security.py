from __future__ import annotations

import pytest
from conftest import PASSWORD, SECRET, Item, ItemAdmin, Owner, Panel, User, build_client, csrf_of
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from fastapi_panel import AdminSite, ModelAuth, StaticAuth, hash_password
from fastapi_panel.security import Signer
from fastapi_panel.site import _is_safe_next, session_cookie_name

COOKIE_NAME = session_cookie_name(SECRET)


def test_every_page_requires_login(anon):
    for url in (
        "/",
        "/item",
        "/item/add",
        "/item/1/change",
        "/item/1/delete",
        "/item/1/history",
        "/owner/autocomplete",
    ):
        response = anon.get(url, follow_redirects=False)
        assert response.status_code == 303, url
        assert "/admin/login" in response.headers["location"]


def test_anonymous_post_changes_nothing(anon, db):
    response = anon.post("/item/1/delete")
    assert response.status_code == 303 and "login" in response.headers["location"]
    assert db.get(Item, 1) is not None


def test_login_rejects_wrong_password_and_non_staff(anon):
    assert anon.login(password="wrong").status_code == 400
    assert anon.login(username="nobody").status_code == 400
    assert anon.login(username="customer").status_code == 400
    assert anon.login(username="gone").status_code == 400


def test_login_errors_do_not_reveal_which_part_failed(anon):
    known = anon.login(password="wrong").text
    unknown = anon.login(username="nobody", password="wrong").text
    assert "correct username and password" in known and "correct username and password" in unknown


def test_login_is_throttled(anon):
    for _ in range(5):
        assert anon.login(password="wrong").status_code == 400
    blocked = anon.login(password=PASSWORD)
    assert blocked.status_code == 429


def test_post_without_csrf_token_is_refused(panel, db):
    response = panel.post("/item/1/delete", csrf=False)
    assert response.status_code == 403
    assert db.get(Item, 1) is not None


def test_post_with_wrong_csrf_token_is_refused(panel, db):
    response = panel.client.post("/admin/item/1/delete", data={"csrf_token": "guess"})
    assert response.status_code == 403
    assert db.get(Item, 1) is not None


def test_login_form_is_csrf_protected(anon):
    assert anon.post("/login", {"username": "root", "password": PASSWORD}, csrf=False).status_code == 403


def test_cross_origin_post_is_refused(panel, db):
    response = panel.post("/item/1/delete", headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert db.get(Item, 1) is not None
    assert panel.post("/item/1/delete", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert panel.post("/item/1/delete", headers={"origin": "null"}).status_code == 403


def test_same_origin_post_is_allowed(panel, db):
    response = panel.post("/item/1/delete", headers={"origin": "http://testserver"})
    assert response.status_code == 303


def test_tampered_session_cookie_is_ignored(panel):
    cookie = panel.client.cookies[COOKIE_NAME]
    payload, _, signature = cookie.partition(".")
    panel.client.cookies.clear()
    panel.client.cookies.set(COOKIE_NAME, f"{payload}x.{signature}", path="/admin")
    assert panel.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize(
    "stale",
    [
        Signer("another-project-on-localhost").dumps({"csrf": "theirs"}),
        Signer(SECRET).dumps({"csrf": "old", "uid": "1", "hash": "x", "exp": 1}),
    ],
    ids=["signed with another key", "expired"],
)
def test_login_works_with_a_stale_cookie_in_the_browser(engine, stale):
    _, app = build_client(engine)
    client = TestClient(app, follow_redirects=False)
    page = client.get("/admin/login", headers={"cookie": f"{COOKIE_NAME}={stale}"})
    replaced = page.cookies[COOKIE_NAME]
    client.cookies.clear()
    response = client.post(
        "/admin/login",
        data={"username": "root", "password": PASSWORD, "csrf_token": csrf_of(page.text)},
        headers={"cookie": f"{COOKIE_NAME}={replaced}"},
    )
    assert response.status_code == 303


def test_projects_on_one_host_do_not_overwrite_each_others_session(engine):
    _, app = build_client(engine)
    other_site = AdminSite()
    other_site.register(Item)
    other_app = FastAPI()
    other_site.mount(other_app, engine=engine, auth=ModelAuth(User), secret_key="y" * 40)
    project, other = TestClient(app, follow_redirects=False), TestClient(other_app)

    page = project.get("/admin/login")
    # Browsers ignore the port when matching cookies, so both projects share one cookie jar on localhost.
    other.cookies.update(project.cookies)
    other.get("/admin/login")
    project.cookies.update(other.cookies)

    response = project.post(
        "/admin/login", data={"username": "root", "password": PASSWORD, "csrf_token": csrf_of(page.text)}
    )
    assert response.status_code == 303


def test_cookie_flags(anon):
    header = anon.get("/login").headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header and "path=/admin" in header


def test_cookie_is_secure_over_https(app):
    client = TestClient(app, base_url="https://testserver")
    assert "secure" in client.get("/admin/login").headers["set-cookie"].lower()


def test_security_headers_on_every_response(panel):
    for response in (panel.get("/"), panel.get("/nope"), panel.get("/item/9999/change")):
        headers = response.headers
        assert "default-src 'none'" in headers["content-security-policy"]
        assert "'unsafe-inline'" not in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["cache-control"] == "no-store"


def test_pages_have_no_inline_scripts_or_styles(panel):
    for url in ("/", "/item", "/item/add", "/item/1/change", "/item/1/delete", "/password"):
        html = panel.get(url).text
        assert "<script>" not in html, url
        assert " style=" not in html, url
        assert " onclick=" not in html, url


def test_html_is_escaped(panel, db):
    db.add(Item(name="<script>alert(1)</script>", price=1))
    db.commit()
    page = panel.get("/item?q=script")
    assert "<script>alert(1)" not in page.text
    assert "&lt;script&gt;" in page.text


def test_search_query_is_escaped(panel):
    assert '"><script>' not in panel.get('/item?q="><script>alert(1)</script>').text


def test_open_redirect_is_blocked():
    assert _is_safe_next("/admin/item", "/admin")
    for target in ("https://evil.example", "//evil.example", "/admin\\@evil.example", "/other", ""):
        assert not _is_safe_next(target, "/admin"), target


def test_login_ignores_external_next(anon):
    page = anon.get("/login?next=https://evil.example")
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    response = anon.client.post(
        "/admin/login?next=https://evil.example",
        data={"csrf_token": csrf, "username": "root", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/admin/"


def test_logout_needs_post_and_ends_the_session(panel):
    assert panel.get("/logout").status_code in (404, 405)
    assert panel.get("/", follow_redirects=False).status_code == 200
    assert panel.post("/logout").status_code == 303
    assert panel.get("/", follow_redirects=False).status_code == 303


def test_view_only_user_cannot_change_anything(anon, db):
    anon.login(username="viewer")
    assert anon.get("/item").status_code == 200
    page = anon.get("/item/1/change")
    assert page.status_code == 200 and "_save" not in page.text
    assert anon.get("/item/add").status_code == 403
    assert anon.post("/item/1/change", {"name": "hacked", "price": "1", "status": "DRAFT"}).status_code == 403
    assert anon.post("/item/1/delete").status_code == 403
    assert anon.post("/item", {"_save_list": "1", "_row": "1", "row-1-price": "5"}).status_code == 403
    db.expire_all()
    assert db.get(Item, 1).name == "item0"


def test_actions_are_filtered_by_permission(anon, db):
    anon.login(username="viewer")
    page = anon.get("/item")
    assert "delete_selected" not in page.text and "activate" not in page.text and "export_csv" in page.text
    anon.post("/item", {"action": "delete_selected", "_selected": "1", "_confirm": "1"})
    db.expire_all()
    assert db.get(Item, 1) is not None


def test_permission_is_per_model(anon):
    anon.login(username="editor")
    assert anon.get("/item").status_code == 200
    assert anon.get("/owner").status_code == 403
    assert anon.get("/owner/autocomplete?q=a").status_code == 403


def test_user_without_access_sees_no_nav(anon):
    anon.login(username="viewer")
    page = anon.get("/")
    assert ">Owners<" not in page.text


def test_row_level_scoping_covers_every_view(engine, db):
    class ScopedItemAdmin(ItemAdmin):
        def get_queryset(self, request):
            return super().get_queryset(request).where(Item.active.is_(True))

    site = AdminSite()
    site.register(Item, ScopedItemAdmin)
    app = FastAPI()
    site.mount(app, engine=engine, auth=ModelAuth(User, superuser_field="is_superuser"), secret_key="x" * 40)
    scoped = Panel(TestClient(app), engine)
    scoped.login()
    hidden = db.scalar(select(Item).where(Item.active.is_(False)))
    assert scoped.get(f"/item/{hidden.id}/change").status_code == 404
    assert scoped.post(f"/item/{hidden.id}/delete").status_code == 404
    assert scoped.get(f"/item/{hidden.id}/history").status_code == 404
    scoped.post("/item", {"action": "delete_selected", "_selected": str(hidden.id), "_confirm": "1"})
    db.expire_all()
    assert db.get(Item, hidden.id) is not None


def test_password_change_kills_other_sessions(engine, app, panel):
    other = Panel(TestClient(app), engine)
    other.login()
    assert other.get("/", follow_redirects=False).status_code == 200

    new = "a much newer password"
    response = panel.post("/password", {"current": PASSWORD, "new": new, "confirm": new})
    assert response.status_code == 303
    assert panel.get("/", follow_redirects=False).status_code == 200
    assert other.get("/", follow_redirects=False).status_code == 303


def test_password_change_validates(panel):
    wrong = panel.post("/password", {"current": "nope", "new": "long enough pw", "confirm": "long enough pw"})
    assert "not your current password" in wrong.text
    short = panel.post("/password", {"current": PASSWORD, "new": "x", "confirm": "x"})
    assert "at least 8" in short.text


def test_disabled_user_loses_access_right_away(panel, db):
    db.scalar(select(User).where(User.username == "root")).is_active = False
    db.commit()
    assert panel.get("/", follow_redirects=False).status_code == 303


def test_short_secret_is_refused():
    with pytest.raises(ValueError, match="secret_key"):
        AdminSite().mount(FastAPI(), engine=None, auth=StaticAuth({}), secret_key="short")


def test_model_auth_fails_closed_without_staff_field():
    with pytest.raises(ValueError, match="no field"):
        ModelAuth(Owner, username_field="name", password_field="secret_token")


def test_static_auth_refuses_plain_passwords():
    with pytest.raises(ValueError, match="must be a hash"):
        StaticAuth({"admin": "admin"})


def test_static_auth_logs_in(engine):
    site = AdminSite()
    site.register(Item)
    app = FastAPI()
    site.mount(app, engine=engine, auth=StaticAuth({"boss": hash_password(PASSWORD)}), secret_key="x" * 40)
    panel = Panel(TestClient(app), engine)
    assert panel.login("boss").status_code == 303
    assert panel.get("/item").status_code == 200


def test_oversized_body_is_refused(engine):
    _, app = build_client(engine, max_body_size=10)
    panel = Panel(TestClient(app), engine)
    assert panel.post("/login", {"username": "root", "password": PASSWORD}).status_code == 413


def test_secret_rotation_ends_sessions(engine):
    _, app = build_client(engine)
    panel = Panel(TestClient(app), engine)
    panel.login()
    cookie = panel.client.cookies[COOKIE_NAME]

    other_site = AdminSite()
    other_site.register(Item)
    other_app = FastAPI()
    other_site.mount(other_app, engine=engine, auth=ModelAuth(User), secret_key="y" * 40)
    other = TestClient(other_app, cookies={session_cookie_name("y" * 40): cookie})
    assert other.get("/admin/", follow_redirects=False).status_code == 303
