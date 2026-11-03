from __future__ import annotations

import datetime as dt
import enum
import re
from decimal import Decimal

import pytest
from fastapi import FastAPI
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Numeric, String, Table, Text, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from fastapi_panel import (
    AdminSite,
    ModelAdmin,
    ModelAuth,
    SimpleFilter,
    TabularInline,
    ValidationError,
    action,
    display,
    hash_password,
)

SECRET = "x" * 40
PASSWORD = "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)


class Base(DeclarativeBase):
    pass


class Status(enum.Enum):
    DRAFT = "draft"
    LIVE = "live"


item_tags = Table(
    "item_tags",
    Base.metadata,
    Column("item_id", ForeignKey("items.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_staff: Mapped[bool] = mapped_column(default=True)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    permissions: Mapped[list | None] = mapped_column(JSON)

    def __str__(self) -> str:
        return self.username


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    secret_token: Mapped[str | None] = mapped_column(String(50))


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    body: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("1.00"))
    active: Mapped[bool] = mapped_column(default=True)
    status: Mapped[Status] = mapped_column(default=Status.DRAFT)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    owner: Mapped[Owner | None] = relationship()
    tags: Mapped[list[Tag]] = relationship(secondary=item_tags)
    notes: Mapped[list[Note]] = relationship(back_populates="item", cascade="all, delete-orphan")

    def __str__(self) -> str:
        return self.name


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(100))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))

    item: Mapped[Item] = relationship(back_populates="notes")


class Natural(Base):
    __tablename__ = "naturals"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    region: Mapped[str] = mapped_column(String(10), primary_key=True)
    label: Mapped[str] = mapped_column(String(50))


class CheapFilter(SimpleFilter):
    title = "Cheap"
    parameter = "cheap"

    def lookups(self, request):
        return [("yes", "Under 5")]

    def queryset(self, request, stmt, value):
        return stmt.where(Item.price < 5)


class NoteInline(TabularInline):
    model = Note
    extra = 1
    max_num = 3


class ItemAdmin(ModelAdmin):
    list_display = ("name", "owner", "price", "active", "shout")
    list_editable = ("price",)
    list_filter = ("active", "status", "owner", "tags", CheapFilter, "created_at")
    search_fields = ("name", "owner__name")
    ordering = ("name",)
    date_hierarchy = "created_at"
    list_per_page = 5
    inlines = (NoteInline,)
    actions = ("activate", "needs_check")
    save_as = True

    @display("Shout", ordering="name")
    def shout(self, obj):
        return obj.name.upper()

    @action("Activate")
    def activate(self, session, request, objects):
        for obj in objects:
            obj.active = True
        self.message_user(request, f"Activated {len(objects)}")

    @action("Needs check", permissions=("delete",), confirm="Sure?")
    def needs_check(self, session, request, objects):
        pass

    def clean(self, data, obj=None):
        if data.get("name") == "forbidden":
            raise ValidationError("Not this name.", field="name")


class OwnerAdmin(ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


class TagAdmin(ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


class NaturalAdmin(ModelAdmin):
    list_display = ("code", "region", "label")


def make_site() -> AdminSite:
    site = AdminSite(title="Test")
    site.register(Item, ItemAdmin)
    site.register(Owner, OwnerAdmin)
    site.register(Tag, TagAdmin)
    site.register(Natural, NaturalAdmin)
    return site


def seed(session: Session) -> None:
    session.add_all(
        [
            User(username="root", password=PASSWORD_HASH, is_superuser=True),
            User(username="viewer", password=PASSWORD_HASH, permissions=["item.view"]),
            User(username="editor", password=PASSWORD_HASH, permissions=["item.*"]),
            User(username="customer", password=PASSWORD_HASH, is_staff=False),
            User(username="gone", password=PASSWORD_HASH, is_active=False),
        ]
    )
    alice = Owner(name="Alice", secret_token="hunter2")
    session.add_all([alice, Owner(name="Bob"), Tag(name="red"), Tag(name="blue")])
    session.flush()
    for i in range(7):
        session.add(Item(name=f"item{i}", price=Decimal(i + 1), active=i % 2 == 0, owner=alice if i < 2 else None))
    session.add(Natural(code="a/b,c", region="eu", label="odd key"))
    session.commit()


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


class Panel:
    """A logged-in client plus the small helpers every test wants."""

    def __init__(self, client: TestClient, engine) -> None:
        self.client = client
        self.engine = engine

    def get(self, url, **kwargs):
        return self.client.get(f"/admin{url}", **kwargs)

    def post(self, url, data=None, csrf=True, **kwargs):
        payload = dict(data or {})
        if csrf:
            page = self.client.get("/admin/login", follow_redirects=True)
            payload["csrf_token"] = csrf_of(page.text)
        return self.client.post(f"/admin{url}", data=payload, follow_redirects=False, **kwargs)

    def login(self, username="root", password=PASSWORD):
        return self.post("/login", {"username": username, "password": password})


def build_client(engine_bind, **mount_kwargs):
    site = make_site()
    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine_bind, auth=auth, secret_key=SECRET, **mount_kwargs)
    return site, app


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
    return engine


@pytest.fixture
def db(engine):
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def app(engine):
    _, app = build_client(engine)
    return app


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://testserver")


@pytest.fixture
def anon(client, engine):
    return Panel(client, engine)


@pytest.fixture
def panel(anon):
    response = anon.login()
    assert response.status_code == 303
    return anon
