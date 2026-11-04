from __future__ import annotations

import pytest
from conftest import PASSWORD_HASH, SECRET, Item, ItemAdmin, Panel, User, make_site
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from fastapi_panel import AdminLog, AdminSite, ImproperlyConfigured, ModelAuth, ValidationError, action


class ButtonAdmin(ItemAdmin):
    object_actions = ("rename", "refuse", "download", "crash", "inactive_only", "needs_delete")

    @action("Rename it", confirm="Rename <this>?")
    def rename(self, session, request, obj):
        obj.name = "renamed"
        self.message_user(request, "Renamed.")

    @action("Refuse")
    def refuse(self, session, request, obj):
        obj.name = "must not stick"
        raise ValidationError("Not allowed right now.")

    @action("Download")
    def download(self, session, request, obj):
        return PlainTextResponse(f"file for {obj.name}")

    @action("Crash")
    def crash(self, session, request, obj):
        session.flush()
        raise IntegrityError("x", {}, Exception("boom"))

    @action("Inactive only")
    def inactive_only(self, session, request, obj):
        obj.body = "ran"

    @action("Needs delete", permissions=("delete",))
    def needs_delete(self, session, request, obj):
        obj.body = "deleted power"

    def get_object_actions(self, request, obj):
        found = super().get_object_actions(request, obj)
        if obj.active:
            found.pop("inactive_only", None)
        return found


class ScopedAdmin(ButtonAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).where(Item.name != "item0")


def mount(engine, admin_class=ButtonAdmin):
    site = make_site()
    site.unregister(Item)
    site.register(Item, admin_class)
    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key=SECRET)
    return app


def login(app, engine, username="root"):
    panel = Panel(TestClient(app), engine)
    panel.login(username)
    return panel


@pytest.fixture
def panel(engine):
    return login(mount(engine), engine)


def item(engine, name="item0"):
    with Session(engine) as session:
        return session.scalar(select(Item).where(Item.name == name))


def test_buttons_appear_with_confirm_and_are_escaped(panel, engine):
    page = panel.get(f"/item/{item(engine).id}/change").text
    assert f'action="/admin/item/{item(engine).id}/action/rename"' in page
    assert 'data-confirm="Rename &lt;this&gt;?"' in page and ">Rename it</button>" in page


def test_no_buttons_on_the_add_page(panel):
    assert "/action/" not in panel.get("/item/add").text


def test_running_saves_logs_and_returns_to_the_record(panel, engine):
    pk = item(engine).id
    response = panel.post(f"/item/{pk}/action/rename")
    assert response.status_code == 303 and response.headers["location"].endswith(f"/item/{pk}/change")
    assert "Renamed." in panel.get(f"/item/{pk}/change").text
    assert item(engine, "renamed").id == pk
    with Session(engine) as session:
        entry = session.scalars(select(AdminLog).where(AdminLog.object_id == str(pk))).one()
        assert entry.action == "change" and "Rename it" in entry.message


def test_validation_error_undoes_changes_and_shows_the_message(panel, engine):
    pk = item(engine).id
    assert panel.post(f"/item/{pk}/action/refuse").status_code == 303
    assert "Not allowed right now." in panel.get(f"/item/{pk}/change").text
    assert item(engine).id == pk


def test_database_error_is_reported(panel, engine):
    pk = item(engine).id
    assert panel.post(f"/item/{pk}/action/crash").status_code == 303
    assert "conflicts with existing data" in panel.get(f"/item/{pk}/change").text


def test_a_response_is_sent_back(panel, engine):
    assert panel.post(f"/item/{item(engine).id}/action/download").text == "file for item0"


def test_get_and_missing_csrf_do_nothing(panel, engine):
    pk = item(engine).id
    assert panel.get(f"/item/{pk}/action/rename").status_code == 405
    assert panel.post(f"/item/{pk}/action/rename", csrf=False).status_code == 403
    assert item(engine).id == pk


def test_only_listed_methods_can_run(panel, engine):
    pk = item(engine).id
    for name in ("delete_model", "missing", "activate"):
        assert panel.post(f"/item/{pk}/action/{name}").status_code == 404


def test_action_hidden_for_this_record_cant_be_posted(panel, engine):
    pk = item(engine).id  # item0 is active
    assert "/action/inactive_only" not in panel.get(f"/item/{pk}/change").text
    assert panel.post(f"/item/{pk}/action/inactive_only").status_code == 404
    inactive = item(engine, "item1").id
    assert "/action/inactive_only" in panel.get(f"/item/{inactive}/change").text
    assert panel.post(f"/item/{inactive}/action/inactive_only").status_code == 303


def test_permissions_are_checked(engine):
    with Session(engine) as session:
        session.add(User(username="changer", password=PASSWORD_HASH, permissions=["item.change"]))
        session.commit()
    app = mount(engine)
    pk = item(engine).id
    viewer = login(app, engine, "viewer")
    assert "/action/" not in viewer.get(f"/item/{pk}/change").text
    assert viewer.post(f"/item/{pk}/action/rename").status_code == 404
    changer = login(app, engine, "changer")
    page = changer.get(f"/item/{pk}/change").text
    assert "/action/rename" in page and "/action/needs_delete" not in page
    assert changer.post(f"/item/{pk}/action/needs_delete").status_code == 404
    assert item(engine).body is None


def test_rows_outside_the_queryset_are_404(engine):
    panel = login(mount(engine, ScopedAdmin), engine)
    assert panel.post(f"/item/{item(engine).id}/action/rename").status_code == 404
    assert item(engine).id


def test_typo_fails_at_startup(engine):
    class Broken(ItemAdmin):
        object_actions = ("nope",)

    site = AdminSite()
    site.register(Item, Broken)
    auth = ModelAuth(User, superuser_field="is_superuser")
    with pytest.raises(ImproperlyConfigured, match="object_actions"):
        site.mount(FastAPI(), engine=engine, auth=auth, secret_key=SECRET)
