from __future__ import annotations

import pytest
from conftest import SECRET, Base, Panel, User, seed
from fastapi import FastAPI
from sqlalchemy import Column, ForeignKey, String, Table, create_engine, event, select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from fastapi_panel import AdminSite, ModelAdmin, ModelAuth

shelf_labels = Table(
    "shelf_labels",
    Base.metadata,
    Column("shelf_id", ForeignKey("shelves.id"), primary_key=True),
    Column("sticker_id", ForeignKey("stickers.id", ondelete="CASCADE"), primary_key=True),
)
bare_links = Table(
    "bare_links",
    Base.metadata,
    Column("sticker_id", ForeignKey("stickers.id"), primary_key=True),
    Column("value", String(20), primary_key=True),
)


class Sticker(Base):
    __tablename__ = "stickers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class Shelf(Base):
    __tablename__ = "shelves"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    books: Mapped[list[Book]] = relationship(back_populates="shelf", cascade="all, delete-orphan")
    stickers: Mapped[list[Sticker]] = relationship(secondary=shelf_labels)


class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    shelf_id: Mapped[int] = mapped_column(ForeignKey("shelves.id"))
    shelf: Mapped[Shelf] = relationship(back_populates="books")
    chapters: Mapped[list[Chapter]] = relationship(back_populates="book", cascade="all, delete-orphan")


class Chapter(Base):
    __tablename__ = "chapters"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"))
    book: Mapped[Book] = relationship(back_populates="chapters")


class Author(Base):
    """Referenced three ways: blocked with no relationship, by a DB cascade, and by SET NULL."""

    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))


class Bio(Base):
    __tablename__ = "bios"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id", ondelete="CASCADE"))


class Mention(Base):
    __tablename__ = "mentions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("authors.id", ondelete="SET NULL"))


class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("folders.id"))
    parent: Mapped[Folder | None] = relationship(remote_side=[id])


class Loop(Base):
    __tablename__ = "loops"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    next_id: Mapped[int | None] = mapped_column(ForeignKey("loops.id", ondelete="CASCADE"))


class ScopedContractAdmin(ModelAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).where(Contract.name != "secret contract")


class KeepAuthorAdmin(ModelAdmin):
    def delete_model(self, session, request, obj):
        obj.name = "archived"


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
        shelf = Shelf(name="Shelf one", stickers=[Sticker(name="Sale")])
        book = Book(name="Dune", shelf=shelf, chapters=[Chapter(name="Chapter 1"), Chapter(name="Chapter 2")])
        author, free = Author(name="Frank"), Author(name="Free")
        session.add_all([book, author, free])
        session.flush()
        session.add_all(
            [
                Bio(name="Short bio", author_id=author.id),
                Mention(name="A mention", author_id=author.id),
                Bio(name="Free bio", author_id=free.id),
                Mention(name="Free mention", author_id=free.id),
            ]
        )
        session.add(Contract(name="<b>Deal</b>", author_id=author.id))
        session.commit()
    return engine


def mount(engine, *registrations):
    site = AdminSite()
    for model, admin_class in registrations:
        site.register(model, admin_class)
    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key=SECRET)
    panel = Panel(TestClient(app), engine)
    panel.login()
    return panel


@pytest.fixture
def panel(engine):
    return mount(
        engine,
        (Shelf, ModelAdmin),
        (Book, ModelAdmin),
        (Sticker, ModelAdmin),
        (Author, ModelAdmin),
        (Contract, ModelAdmin),
        (Folder, ModelAdmin),
    )


def exists(engine, model, pk=1):
    with Session(engine) as session:
        return session.get(model, pk) is not None


def test_tree_shows_nested_cascades_with_counts_and_links(panel):
    page = panel.get("/shelf/1/delete").text
    assert '<a href="/admin/book/1/change">Dune</a>' in page
    assert "Chapter 1" in page and "Chapter 2" in page and "<details open>" in page
    assert "Chapters: <strong>2</strong>" in page and "Books: <strong>1</strong>" in page
    assert "Yes, delete" in page and "delete-blocked" not in page


def test_many_to_many_rows_are_links_not_records(panel, engine):
    page = panel.get("/shelf/1/delete").text
    assert "Shelf labels" in page and "Only the links are removed" in page
    assert 'Link to <a href="/admin/sticker/1/change">Sale</a>' in page
    assert panel.post("/shelf/1/delete").status_code == 303
    assert not exists(engine, Shelf) and not exists(engine, Book) and exists(engine, Sticker)


def test_database_cascade_listed_and_set_null_not(panel, engine):
    page = panel.get("/author/2/delete").text
    assert "Free bio" in page and "Free mention" not in page and "Yes, delete" in page
    assert panel.post("/author/2/delete").status_code == 303
    assert not exists(engine, Author, 2)


def test_reference_with_no_relationship_blocks_and_replaces_the_button(panel, engine):
    page = panel.get("/author/1/delete").text
    assert "This can't be deleted yet" in page and "Yes, delete" not in page
    assert '<a href="/admin/contract/1/change">&lt;b&gt;Deal&lt;/b&gt;</a>' in page
    assert panel.post("/author/1/delete").status_code == 303
    assert exists(engine, Author)
    assert "depend on it" in panel.get("/author/1/delete").text


def test_bulk_is_refused_when_any_row_is_blocked(panel, engine):
    page = panel.post("/author", {"action": "delete_selected", "_selected": ["1", "2"]}).text
    assert "This can't be deleted yet" in page and "Yes, delete" not in page
    panel.post("/author", {"action": "delete_selected", "_selected": ["1", "2"], "_confirm": "1"})
    assert exists(engine, Author, 1) and exists(engine, Author, 2)


def test_bulk_ignores_a_blocker_that_is_also_being_deleted(panel, engine):
    with Session(engine) as session:
        session.add_all([Folder(id=1, name="root"), Folder(id=2, name="child", parent_id=1)])
        session.commit()
    assert "This can't be deleted yet" in panel.get("/folder/1/delete").text
    page = panel.post("/folder", {"action": "delete_selected", "_selected": ["1", "2"]}).text
    assert "Yes, delete" in page
    panel.post("/folder", {"action": "delete_selected", "_selected": ["1", "2"], "_confirm": "1"})
    assert not exists(engine, Folder, 1) and not exists(engine, Folder, 2)


def test_cycles_finish_and_list_each_row_once(engine):
    with Session(engine) as session:
        first, second = Loop(id=1, name="first"), Loop(id=2, name="second")
        session.add_all([first, second])
        session.flush()
        first.next_id, second.next_id = 2, 1
        session.commit()
    panel = mount(engine, (Loop, ModelAdmin))
    page = panel.get("/loop/1/delete").text
    assert page.count("second") == 1 and "Loops: <strong>1</strong>" in page


def test_rows_outside_the_users_scope_stay_hidden(engine):
    with Session(engine) as session:
        session.execute(Contract.__table__.update().values(name="secret contract"))
        session.commit()
    panel = mount(engine, (Author, ModelAdmin), (Contract, ScopedContractAdmin))
    page = panel.get("/author/1/delete").text
    assert "secret contract" not in page and "Hidden contract" in page and "/contract/1/change" not in page


def test_custom_delete_model_is_not_blocked_up_front(engine):
    panel = mount(engine, (Author, KeepAuthorAdmin), (Contract, ModelAdmin))
    assert "Yes, delete" in panel.get("/author/1/delete").text
    assert panel.post("/author/1/delete").status_code == 303
    with Session(engine) as session:
        assert session.scalar(select(Author.name).where(Author.id == 1)) == "archived"
