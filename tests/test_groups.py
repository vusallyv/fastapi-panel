from __future__ import annotations

import pytest
from conftest import PASSWORD_HASH, SECRET, Panel, User, make_site
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from fastapi_panel import ModelAdmin, ModelAuth
from fastapi_panel.log import StaffGroup, StaffGroupMember


def build(engine, **auth_options):
    site = make_site()
    app = FastAPI()
    options = {"superuser_field": "is_superuser", "groups": True, **auth_options}
    site.mount(app, engine=engine, auth=ModelAuth(User, **options), secret_key=SECRET)
    return app


def login(app, engine, username):
    panel = Panel(TestClient(app), engine)
    panel.login(username)
    return panel


@pytest.fixture
def app(engine):
    with Session(engine) as session:
        session.add_all(
            [
                User(username="ana", password=PASSWORD_HASH),
                User(username="ben", password=PASSWORD_HASH),
                User(username="wild", password=PASSWORD_HASH, permissions=["*"]),
            ]
        )
        session.commit()
    return build(engine)


@pytest.fixture
def root(app, engine):
    return login(app, engine, "root")


def user_id(engine, username):
    with Session(engine) as session:
        return str(session.scalar(select(User.id).where(User.username == username)))


def make_group(root, engine, name="Support", permissions=("item.view",), members=("ana",)):
    response = root.post(
        "/staff-groups/add",
        {"name": name, "permissions": list(permissions), "members": [user_id(engine, m) for m in members]},
    )
    assert response.status_code == 303, response.text
    with Session(engine) as session:
        return session.scalar(select(StaffGroup.id).where(StaffGroup.name == name))


def test_form_shows_a_grid_of_models_and_pages(root):
    page = root.get("/staff-groups/add").text
    for value in ("item.view", "item.add", "item.change", "item.delete", "owner.view", "activity.view"):
        assert f'value="{value}"' in page
    assert "staff-groups.view" not in page and "ana" in page and "customer" not in page


def test_members_get_the_groups_permissions_and_nobody_else(root, app, engine):
    make_group(root, engine, permissions=("item.view",))
    ana, ben = login(app, engine, "ana"), login(app, engine, "ben")
    assert ana.get("/item").status_code == 200
    assert ana.get("/item/add").status_code == 403 and ana.get("/owner").status_code == 403
    assert ben.get("/item").status_code == 403
    assert "/admin/item" in ana.get("/").text and "/admin/item" not in ben.get("/").text


def test_permissions_from_several_groups_add_up(root, app, engine):
    make_group(root, engine, "One", ("item.view",))
    make_group(root, engine, "Two", ("owner.view",))
    ana = login(app, engine, "ana")
    assert ana.get("/item").status_code == 200 and ana.get("/owner").status_code == 200


def test_changes_apply_on_the_next_request(root, app, engine):
    pk = make_group(root, engine)
    ana = login(app, engine, "ana")
    assert ana.get("/item").status_code == 200
    root.post(f"/staff-groups/{pk}/change", {"name": "Support", "permissions": ["item.view"], "members": []})
    assert ana.get("/item").status_code == 403


def test_only_superusers_manage_groups(root, app, engine):
    pk = make_group(root, engine, permissions=("item.view",))
    for name in ("ana", "wild"):
        user = login(app, engine, name)
        assert user.get("/staff-groups").status_code == 403
        assert user.get(f"/staff-groups/{pk}/change").status_code == 403
        assert user.post("/staff-groups/add", {"name": "Mine", "permissions": ["*"]}).status_code == 403
    with Session(engine) as session:
        assert session.scalar(select(StaffGroup).where(StaffGroup.name == "Mine")) is None


def test_group_permissions_add_to_a_permissions_field(root, engine):
    make_group(root, engine, permissions=("owner.view",), members=("viewer",))
    app = build(engine, permissions_field="permissions")
    viewer = login(app, engine, "viewer")
    assert viewer.get("/item").status_code == 200 and viewer.get("/owner").status_code == 200


def test_non_staff_and_bad_permissions_are_refused(root, engine):
    outsider = user_id(engine, "customer")
    page = root.post("/staff-groups/add", {"name": "Bad", "members": [outsider]})
    assert page.status_code == 200 and "Only staff" in page.text
    page = root.post("/staff-groups/add", {"name": "Bad", "permissions": ["not a permission"]})
    assert page.status_code == 200 and "valid permission" in page.text


def test_saving_keeps_deactivated_members_and_outside_permissions(root, engine):
    pk = make_group(root, engine)
    ana = user_id(engine, "ana")
    with Session(engine) as session:
        session.scalars(select(User).where(User.username == "ana")).one().is_active = False
        session.get(StaffGroup, pk).permissions = ["item.view", "order.*"]
        session.commit()
    page = root.get(f"/staff-groups/{pk}/change").text
    assert f'<option value="{ana}" selected>ana</option>' in page
    assert 'value="order.*" checked' in page
    root.post(f"/staff-groups/{pk}/change", {"name": "Support", "permissions": ["order.*"], "members": [ana]})
    with Session(engine) as session:
        group = session.get(StaffGroup, pk)
        assert group.permissions == ["order.*"] and [m.user_id for m in group.members] == [ana]


def test_deleting_a_user_removes_their_memberships(root, engine):
    make_group(root, engine)
    site = make_site()
    site.register(User, ModelAdmin)
    app = FastAPI()
    site.mount(app, engine=engine, auth=ModelAuth(User, superuser_field="is_superuser", groups=True), secret_key=SECRET)
    panel = login(app, engine, "root")
    assert panel.post(f"/user/{user_id(engine, 'ana')}/delete").status_code == 303
    with Session(engine) as session:
        assert session.scalars(select(StaffGroupMember)).all() == []


def test_deleting_a_group_lists_its_members_and_keeps_the_users(root, engine):
    pk = make_group(root, engine)
    assert "Yes, delete" in root.get(f"/staff-groups/{pk}/delete").text
    assert root.post(f"/staff-groups/{pk}/delete").status_code == 303
    with Session(engine) as session:
        assert session.get(StaffGroup, pk) is None
        assert session.scalars(select(StaffGroupMember)).all() == []
        assert session.scalar(select(User).where(User.username == "ana")) is not None


def test_groups_need_a_superuser_field():
    with pytest.raises(ValueError, match="superuser_field"):
        ModelAuth(User, groups=True)


def test_groups_are_off_by_default(engine):
    site = make_site()
    app = FastAPI()
    site.mount(app, engine=engine, auth=ModelAuth(User, superuser_field="is_superuser"), secret_key=SECRET)
    panel = login(app, engine, "root")
    assert panel.get("/staff-groups").status_code == 404
    assert "Staff groups" not in panel.get("/").text
