from __future__ import annotations

import pytest
from conftest import (
    PASSWORD_HASH,
    SECRET,
    Base,
    Item,
    ItemAdmin,
    Note,
    NoteInline,
    Owner,
    OwnerAdmin,
    Panel,
    User,
    seed,
)
from fastapi import FastAPI
from sqlalchemy import String, create_engine, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from fastapi_panel import AdminSite, ImproperlyConfigured, ModelAdmin, ModelAuth, RichTextField, sanitize_html
from fastapi_panel.log import AdminLog, SavedFilter


def mount(engine, *registrations, **site_options):
    site = AdminSite(**site_options)
    for model, admin_class in registrations:
        site.register(model, admin_class)
    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key=SECRET)
    return Panel(TestClient(app), engine)


def add_user(engine, username, permissions):
    with Session(engine) as session:
        session.add(User(username=username, password=PASSWORD_HASH, permissions=permissions))
        session.commit()


def last_message(engine):
    with Session(engine) as session:
        return session.scalars(select(AdminLog.message).order_by(AdminLog.id.desc())).first()


def item_id(engine, name="item1"):
    with Session(engine) as session:
        return session.scalars(select(Item.id).where(Item.name == name)).one()


class LoggingAdmin(ItemAdmin):
    log_values = ("price", "active", "owner", "status")
    list_editable = ("price",)
    inlines = ()


def form_data(**changes):
    return {"name": "item1", "price": "1.00", "active": "on", "status": "DRAFT", "_save": "1", **changes}


# History values


def test_values_are_only_logged_when_asked_for(panel, engine):
    pk = item_id(engine)
    panel.post(f"/item/{pk}/change", {**form_data(price="7.50"), "note-TOTAL": "0"})
    message = last_message(engine)
    assert "price" in message and "7.50" not in message


def test_logged_values_show_old_and_new(engine):
    panel = mount(engine, (Item, LoggingAdmin), (Owner, OwnerAdmin))
    panel.login()
    pk = item_id(engine)
    with Session(engine) as session:
        session.get(Item, pk).price = "1.00"
        session.get(Item, pk).active = True
        owner = session.scalars(select(Owner).where(Owner.name == "Bob")).one().id
        session.commit()
    panel.post(f"/item/{pk}/change", form_data(price="7.50", owner=str(owner), status="LIVE", active=""))
    message = last_message(engine)
    assert "Price: 1.00 → 7.50" in message
    assert "Active: Yes → No" in message
    assert "Owner: " in message and "→ Bob" in message
    assert "Status: Draft → Live" in message
    assert "7.50" in panel.get(f"/item/{pk}/history").text


def test_list_editing_logs_values(engine):
    panel = mount(engine, (Item, LoggingAdmin), (Owner, OwnerAdmin))
    panel.login()
    pk = item_id(engine)
    panel.post("/item", {"_save_list": "1", "_row": str(pk), f"row-{pk}-price": "3.25"})
    assert "→ 3.25" in last_message(engine)


def test_inline_rows_log_values(engine):
    class Logged(NoteInline):
        log_values = ("text",)

    class Parent(ItemAdmin):
        inlines = (Logged,)

    panel = mount(engine, (Item, Parent), (Owner, OwnerAdmin))
    panel.login()
    with Session(engine) as session:
        pk = session.scalars(select(Item.id).where(Item.name == "item2")).one()
        note = Note(text="before", item_id=pk)
        session.add(note)
        session.commit()
        note_id = note.id
    panel.post(
        f"/item/{pk}/change",
        form_data(name="item2", **{"note-TOTAL": "1", "note-0-_pk": str(note_id), "note-0-text": "after"}),
    )
    assert "Text: before → after" in last_message(engine)


def test_long_values_are_cut(engine):
    class Logged(ItemAdmin):
        log_values = ("body",)
        inlines = ()

    panel = mount(engine, (Item, Logged), (Owner, OwnerAdmin))
    panel.login()
    pk = item_id(engine)
    panel.post(f"/item/{pk}/change", form_data(body="x" * 500))
    assert len(last_message(engine)) < 300


@pytest.mark.parametrize("name", ["password", "missing"])
def test_credentials_and_unknown_fields_cant_be_logged(engine, name):
    class Bad(ModelAdmin):
        log_values = (name,)

    with pytest.raises(ImproperlyConfigured, match="log_values"):
        mount(engine, (User, Bad))


def test_values_are_escaped_in_the_history(engine):
    class Logged(ItemAdmin):
        log_values = ("body",)
        inlines = ()

    panel = mount(engine, (Item, Logged), (Owner, OwnerAdmin))
    panel.login()
    pk = item_id(engine)
    panel.post(f"/item/{pk}/change", form_data(body="<script>alert(1)</script>"))
    assert "<script>alert(1)" not in panel.get(f"/item/{pk}/history").text
    assert "<script>alert(1)" not in panel.get("/activity").text


# Activity page


def test_activity_lists_everything_and_filters(panel, engine):
    pk = item_id(engine)
    panel.post(f"/item/{pk}/change", {**form_data(price="7.50"), "note-TOTAL": "0"})
    panel.post("/owner/add", {"name": "Zed", "_save": "1"})
    page = panel.get("/activity").text
    assert "Activity" in page and "item1" in page and "Zed" in page
    assert "/admin/activity" in panel.get("/").text
    only_owner = panel.get("/activity?model=owner").text
    assert "Zed" in only_owner and "item1</a>" not in only_owner
    assert "Zed" not in panel.get("/activity?action=delete").text
    assert "Zed" not in panel.get("/activity?user=nobody").text


def test_activity_ignores_unknown_filters(panel):
    assert panel.get("/activity?model=password&action=drop&page=abc").status_code == 200


def test_activity_needs_permission(anon, engine):
    anon.login("editor")
    assert anon.get("/activity").status_code == 403
    assert "/admin/activity" not in anon.get("/").text


def test_activity_hides_models_you_cant_see(panel, anon, engine):
    panel.post("/owner/add", {"name": "Hidden Owner", "_save": "1"})
    panel.post("/logout")
    add_user(engine, "auditor", ["activity.view", "item.view"])
    anon.login("auditor")
    page = anon.get("/activity")
    assert page.status_code == 200 and "Hidden Owner" not in page.text
    assert "Hidden Owner" not in anon.get("/activity?model=owner").text


def test_no_activity_without_the_audit_log(engine):
    panel = mount(engine, (Owner, OwnerAdmin), audit_log=False)
    panel.login()
    assert panel.get("/activity").status_code == 404


# Saved filters


def test_save_open_and_delete_a_filter(panel, engine):
    assert "Save this view" not in panel.get("/item").text
    assert "Save this view" in panel.get("/item?active=1&page=2").text
    response = panel.post("/item/filters", {"name": "Live ones", "query": "active=1&page=2"})
    assert response.status_code == 303 and response.headers["location"] == "/admin/item?active=1"
    page = panel.get("/item").text
    assert "Live ones" in page and 'href="/admin/item?active=1"' in page
    assert "Save this view" not in panel.get("/item?active=1").text

    with Session(engine) as session:
        pk = session.scalars(select(SavedFilter.id)).one()
    panel.post("/item/filters", {"_delete": str(pk), "query": ""})
    assert "Live ones" not in panel.get("/item").text


def test_saving_the_same_name_replaces_it(panel, engine):
    panel.post("/item/filters", {"name": "Mine", "query": "active=1"})
    panel.post("/item/filters", {"name": "Mine", "query": "active=0"})
    with Session(engine) as session:
        assert session.scalars(select(SavedFilter.query)).all() == ["active=0"]


def test_filters_belong_to_one_user_and_one_model(panel, anon, engine):
    panel.post("/item/filters", {"name": "Private", "query": "active=1"})
    panel.get("/item")  # shows the confirmation message once
    assert "Private" not in panel.get("/owner").text
    with Session(engine) as session:
        pk = session.scalars(select(SavedFilter.id)).one()
    panel.post("/logout")
    anon.login("editor")
    assert "Private" not in anon.get("/item").text
    anon.post("/item/filters", {"_delete": str(pk), "query": ""})
    with Session(engine) as session:
        assert session.get(SavedFilter, pk) is not None


def test_saved_query_cant_escape_the_list(panel):
    response = panel.post("/item/filters", {"name": "Odd", "query": "//evil.example/?x=1&q=<script>"})
    assert response.headers["location"].startswith("/admin/item")
    page = panel.get("/item").text
    assert "<script>" not in page and 'href="//evil' not in page


def test_bad_saves_explain_themselves(panel, engine):
    panel.post("/item/filters", {"name": "", "query": "active=1"})
    panel.post("/item/filters", {"name": "x", "query": ""})
    panel.post("/item/filters", {"name": "x", "query": "q=" + "a" * 3000})
    assert "Give it a name" in panel.get("/item").text
    with Session(engine) as session:
        assert session.scalars(select(SavedFilter)).all() == []


def test_saved_filters_need_view_permission(anon):
    anon.login("viewer")
    assert anon.post("/owner/filters", {"name": "x", "query": "q=1"}).status_code == 403
    assert anon.post("/item/filters", {"name": "x", "query": "q=1"}, csrf=False).status_code == 403


def test_list_survives_without_the_table(panel, engine):
    SavedFilter.__table__.drop(engine)
    assert panel.get("/item").status_code == 200
    assert panel.post("/item/filters", {"name": "x", "query": "q=1"}).status_code == 303


# Rich text


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    body: Mapped[str] = mapped_column(String(5000))


class PostAdmin(ModelAdmin):
    list_display = ("title", "body")
    field_overrides = {"body": RichTextField()}
    log_values = ("body",)


@pytest.fixture
def posts():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
    panel = mount(engine, (Post, PostAdmin))
    panel.login()
    return panel


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<p>Hi <b>you</b></p>", "<p>Hi <strong>you</strong></p>"),
        ("<script>alert(1)</script>safe", "safe"),
        ("<SCRIPT SRC=x></SCRIPT>safe", "safe"),
        ('<img src=x onerror="alert(1)">safe', "safe"),
        ('<p onclick="alert(1)" style="x" class="y">a</p>', "<p>a</p>"),
        ('<a href="javascript:alert(1)">x</a>', "x"),
        ('<a href=" java\tscript:alert(1)">x</a>', "x"),
        ('<a href="data:text/html,x">x</a>', "x"),
        (
            '<a href="https://a.example/?a=1&b=2" target="_top">x</a>',
            '<a href="https://a.example/?a=1&amp;b=2" rel="noopener noreferrer">x</a>',
        ),
        ("<svg><script>alert(1)</script></svg>safe", "safe"),
        ("<iframe src=x>inner</iframe>safe", "safe"),
        ("<style>p{}</style>safe", "safe"),
        ("<!-- c --><p>a</p>", "<p>a</p>"),
        ("<p>a &lt;script&gt; b</p>", "<p>a &lt;script&gt; b</p>"),
        ("<ul><li>a<li>b", "<ul><li>a<li>b</li></li></ul>"),
        ("</p></div>a", "a"),
        ("<div>a</div>", "<p>a</p>"),
        ("<h1>a</h1><h4>b</h4>", "<h2>a</h2><h3>b</h3>"),
        ("a<br/>b", "a<br>b"),
    ],
)
def test_sanitizer(raw, clean):
    assert sanitize_html(raw) == clean


def test_sanitizer_output_is_stable_and_bounded():
    bomb = "<b>" * 5000 + "</i>" * 5000
    once = sanitize_html(bomb)
    assert sanitize_html(once) == once and once.count("<strong>") == 50


def test_body_is_sanitized_when_saved(posts):
    posts.post("/post/add", {"title": "t", "body": "<p>ok</p><script>alert(1)</script>", "_save": "1"})
    with Session(posts.engine) as session:
        assert session.scalars(select(Post.body)).one() == "<p>ok</p>"


def test_editor_gets_a_clean_value_even_for_rows_written_elsewhere(posts):
    with Session(posts.engine) as session:
        session.add(Post(title="t", body="<p>ok</p><img src=x onerror=alert(1)>"))
        session.commit()
    page = posts.get("/post/1/change").text
    assert "data-richtext" in page and "onerror" not in page


def test_list_shows_plain_text(posts):
    with Session(posts.engine) as session:
        session.add(Post(title="t", body="<p>Hello <strong>there</strong></p><script>x</script>"))
        session.commit()
    page = posts.get("/post").text
    assert "Hello there" in page and "<strong>there" not in page


def test_empty_editor_counts_as_empty(posts):
    page = posts.post("/post/add", {"title": "t", "body": "<p><br></p>", "_save": "1"})
    assert page.status_code == 200 and "This field is required" in page.text


def test_viewers_see_the_formatted_text(posts):
    with Session(posts.engine) as session:
        session.add(Post(title="t", body="<p>Hi <strong>you</strong><script>x</script></p>"))
        session.add(User(username="reader", password=PASSWORD_HASH, permissions=["post.view"]))
        session.commit()
    posts.post("/logout")
    posts.login("reader")
    page = posts.get("/post/1/change").text
    assert "<strong>you</strong>" in page and "<script>x" not in page


def test_history_shows_text_without_tags(posts):
    posts.post("/post/add", {"title": "t", "body": "<p>one</p>", "_save": "1"})
    posts.post("/post/1/change", {"title": "t", "body": "<p>two</p>", "_save": "1"})
    assert "Body: one → two" in last_message(posts.engine)
