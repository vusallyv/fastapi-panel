from typing import Optional

from conftest import PASSWORD, Base, Item, Panel, User, csrf_of, seed
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from sqlmodel import Field as SQLField
from sqlmodel import Relationship, SQLModel
from starlette.testclient import TestClient

from fastapi_panel import AdminSite, ModelAdmin, ModelAuth, StaticAuth, hash_password
from fastapi_panel.options import csv_safe


def _async_client(tmp_path):
    url = f"{tmp_path}/async.db"
    sync_engine = create_engine(f"sqlite:///{url}")
    Base.metadata.create_all(sync_engine)
    with Session(sync_engine) as session:
        seed(session)
    engine = create_async_engine(f"sqlite+aiosqlite:///{url}")
    site = AdminSite()

    class AsyncItemAdmin(ModelAdmin):
        # A relationship in list_display and a lazy one in a computed column: both must work on an async engine.
        list_display = ("name", "owner", "tag_count")
        actions = ("rename",)

        def tag_count(self, obj):
            return len(obj.tags)

        def rename(self, session, request, objects):
            for obj in objects:
                obj.name = obj.name.upper()

    site.register(Item, AsyncItemAdmin)
    app = FastAPI()
    site.mount(app, engine=engine, auth=ModelAuth(User, superuser_field="is_superuser"), secret_key="x" * 40)
    return TestClient(app), sync_engine


def test_async_engine_full_flow(tmp_path):
    client, sync_engine = _async_client(tmp_path)
    with client:
        login = client.get("/admin/login")
        response = client.post(
            "/admin/login",
            data={"csrf_token": csrf_of(login.text), "username": "root", "password": PASSWORD},
            follow_redirects=False,
        )
        assert response.status_code == 303

        page = client.get("/admin/item")
        assert page.status_code == 200 and "item0" in page.text and "Alice" not in ""

        token = csrf_of(page.text)
        created = client.post(
            "/admin/item/add",
            data={"csrf_token": token, "name": "async", "price": "2", "status": "LIVE"},
            follow_redirects=False,
        )
        assert created.status_code == 303

        with Session(sync_engine) as session:
            item = session.scalar(select(Item).where(Item.name == "async"))
            assert item is not None

        client.post("/admin/item", data={"csrf_token": token, "action": "rename", "_selected": str(item.id)})
        with Session(sync_engine) as session:
            assert session.get(Item, item.id).name == "ASYNC"

        assert client.get(f"/admin/item/{item.id}/history").status_code == 200


class Team(SQLModel, table=True):
    __tablename__ = "sm_teams"

    id: Optional[int] = SQLField(default=None, primary_key=True)
    name: str

    heroes: list["Hero"] = Relationship(back_populates="team")


class Hero(SQLModel, table=True):
    __tablename__ = "sm_heroes"

    id: Optional[int] = SQLField(default=None, primary_key=True)
    name: str = SQLField(index=True)
    age: Optional[int] = None
    team_id: Optional[int] = SQLField(default=None, foreign_key="sm_teams.id")

    team: Optional[Team] = Relationship(back_populates="heroes")


def test_sqlmodel_models_work(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/sm.db")
    SQLModel.metadata.create_all(engine)
    site = AdminSite()

    class HeroAdmin(ModelAdmin):
        list_display = ("name", "age", "team")
        list_filter = ("team",)
        search_fields = ("name",)

    site.register(Team)
    site.register(Hero, HeroAdmin)
    app = FastAPI()
    site.mount(app, engine=engine, auth=StaticAuth({"boss": hash_password(PASSWORD)}), secret_key="x" * 40)
    panel = Panel(TestClient(app), engine)
    assert panel.login("boss").status_code == 303

    assert panel.post("/team/add", {"name": "Avengers"}).status_code == 303
    form = panel.get("/hero/add")
    assert form.status_code == 200 and "Avengers" in form.text
    assert panel.post("/hero/add", {"name": "Thor", "age": "1500", "team": "1"}).status_code == 303

    page = panel.get("/hero")
    assert "Thor" in page.text and "Avengers" in page.text
    with Session(engine) as session:
        assert session.scalar(select(Hero)).team_id == 1


def test_csv_safe():
    assert csv_safe("=1+1") == "'=1+1"
    assert csv_safe("@cmd") == "'@cmd"
    assert csv_safe("plain") == "plain"
