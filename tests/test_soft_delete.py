from __future__ import annotations

import datetime as dt

import pytest
from conftest import SECRET, Base, Panel, User, seed
from fastapi import FastAPI
from sqlalchemy import DateTime, String, create_engine, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from fastapi_panel import AdminSite, ImproperlyConfigured, ModelAdmin, ModelAuth
from fastapi_panel.log import AdminLog


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class Flagged(Base):
    __tablename__ = "flagged"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    is_deleted: Mapped[bool] = mapped_column(default=False)
    locked_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime(2026, 1, 1))


class ArticleAdmin(ModelAdmin):
    list_display = ("title",)
    search_fields = ("title",)
    soft_delete_field = "deleted_at"


class FlaggedAdmin(ModelAdmin):
    list_display = ("title",)
    soft_delete_field = "is_deleted"


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
        session.add_all([Article(title="one"), Article(title="two"), Flagged(title="flag")])
        session.commit()
    return engine


def mount(engine, *registrations):
    site = AdminSite()
    for model, admin_class in registrations:
        site.register(model, admin_class)
    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key=SECRET)
    return Panel(TestClient(app), engine)


@pytest.fixture
def panel(engine):
    panel = mount(engine, (Article, ArticleAdmin), (Flagged, FlaggedAdmin))
    panel.login()
    return panel


def row(engine, model, pk=1):
    with Session(engine) as session:
        return session.get(model, pk)


def test_delete_keeps_the_row_and_hides_it(panel, engine):
    assert "restore these later" in panel.get("/article/1/delete").text
    assert panel.post("/article/1/delete").status_code == 303
    assert row(engine, Article).deleted_at is not None

    page = panel.get("/article").text
    assert "/article/1/change" not in page and "/article/2/change" in page
    assert panel.get("/article/1/change").status_code == 404


def test_deleted_page_lists_only_deleted_rows_and_can_search(panel):
    panel.post("/article/1/delete")
    page = panel.get("/article/deleted").text
    assert "Deleted articles" in page and "one" in page and "two" not in page
    assert "one" not in panel.get("/article/deleted?q=two").text


def test_deleted_rows_link_nowhere_and_cant_be_edited_inline(panel):
    panel.post("/article/1/delete")
    assert "/article/1/change" not in panel.get("/article/deleted").text


def test_restore_one_row(panel, engine):
    panel.post("/article/1/delete")
    assert panel.post("/article/deleted", {"_restore": "1"}).status_code == 303
    assert row(engine, Article).deleted_at is None
    assert "one" in panel.get("/article").text
    with Session(engine) as session:
        assert session.scalars(select(AdminLog.action).order_by(AdminLog.id.desc())).first() == "restore"
    assert "restore" in panel.get("/article/1/history").text


def test_restore_selected_action(panel, engine):
    panel.post("/article/1/delete")
    panel.post("/article/2/delete")
    panel.post("/article/deleted", {"action": "restore_selected", "_selected": ["1", "2"], "_run_action": "1"})
    assert row(engine, Article, 1).deleted_at is None and row(engine, Article, 2).deleted_at is None


def test_delete_on_the_deleted_page_is_permanent(panel, engine):
    panel.post("/article/1/delete")
    confirm = panel.post("/article/deleted", {"action": "delete_selected", "_selected": ["1"], "_run_action": "1"})
    assert "can't be undone" in confirm.text
    panel.post(
        "/article/deleted", {"action": "delete_selected", "_selected": ["1"], "_run_action": "1", "_confirm": "1"}
    )
    assert row(engine, Article) is None


def test_bulk_delete_on_the_list_is_soft(panel, engine):
    panel.post("/article", {"action": "delete_selected", "_selected": ["1"], "_run_action": "1", "_confirm": "1"})
    assert row(engine, Article).deleted_at is not None


def test_boolean_column(panel, engine):
    panel.post("/flagged/1/delete")
    assert row(engine, Flagged).is_deleted is True
    assert "flag" in panel.get("/flagged/deleted").text
    panel.post("/flagged/deleted", {"_restore": "1"})
    assert row(engine, Flagged).is_deleted is False


def test_the_marker_column_is_not_on_the_form(panel):
    assert 'name="deleted_at"' not in panel.get("/article/1/change").text


def test_viewers_cant_open_or_restore(engine):
    panel = mount(engine, (Article, ArticleAdmin))
    with Session(engine) as session:
        session.add(User(username="reader", password=row(engine, User).password, permissions=["article.view"]))
        session.commit()
    panel.login("root")
    panel.post("/article/1/delete")
    panel.post("/logout")
    panel.login("reader")
    assert 'href="/admin/article/deleted"' not in panel.get("/article").text
    assert panel.get("/article/deleted").status_code == 403
    assert panel.post("/article/deleted", {"_restore": "1"}).status_code == 403
    assert row(engine, Article).deleted_at is not None


def test_row_scoping_applies_to_the_deleted_page(engine):
    class Scoped(ArticleAdmin):
        def get_queryset(self, request):
            return super().get_queryset(request).where(Article.id != 1)

    panel = mount(engine, (Article, Scoped))
    panel.login()
    with Session(engine) as session:
        session.get(Article, 1).deleted_at = dt.datetime(2026, 1, 1)
        session.commit()
    assert panel.post("/article/deleted", {"_restore": "1"}).status_code == 404
    assert row(engine, Article).deleted_at is not None


def test_models_without_it_have_no_deleted_page(engine):
    class Plain(ModelAdmin):
        list_display = ("title",)

    panel = mount(engine, (Article, Plain))
    panel.login()
    assert panel.get("/article/deleted").status_code == 404
    assert "/article/deleted" not in panel.get("/article").text


@pytest.mark.parametrize("field", ["title", "missing", "locked_at"])
def test_bad_marker_column_fails_at_startup(engine, field):
    class Bad(ModelAdmin):
        soft_delete_field = field

    with pytest.raises(ImproperlyConfigured, match="soft_delete_field"):
        mount(engine, (Flagged, Bad))
