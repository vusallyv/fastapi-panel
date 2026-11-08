import sys

import pydantic
from conftest import PASSWORD, Item, Note, Owner, Panel, User, build_client
from fastapi import FastAPI
from sqlalchemy import select
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from fastapi_panel import (
    AdminSite,
    CharField,
    ChoiceField,
    ModelAdmin,
    ModelAuth,
    StackedInline,
    ValidationError,
    autodiscover,
    display,
)
from fastapi_panel import site as default_site


def mount(engine, *registrations, **site_options):
    site = AdminSite(**site_options)
    for model, admin_class in registrations:
        site.register(model, admin_class)
    app = FastAPI()
    site.mount(app, engine=engine, auth=ModelAuth(User, superuser_field="is_superuser"), secret_key="x" * 40)
    panel = Panel(TestClient(app), engine)
    assert panel.login().status_code == 303
    return site, panel


def test_custom_page_is_protected_and_listed(engine):
    site = AdminSite()

    @site.page("/reports", title="Sales report", group="Reports", permission="reports.view")
    def report(session, request):
        return PlainTextResponse("secret numbers")

    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key="x" * 40)

    anonymous = TestClient(app).get("/admin/reports", follow_redirects=False)
    assert anonymous.status_code == 303

    viewer = Panel(TestClient(app), engine)
    viewer.login("viewer")
    assert viewer.get("/reports").status_code == 403

    root = Panel(TestClient(app), engine)
    root.login()
    assert root.get("/reports").text == "secret numbers"
    assert "Sales report" in root.get("/").text


def test_dashboard_widgets_respect_permissions_and_survive_errors(engine):
    site = AdminSite()

    @site.widget("Items in stock", url="item?active=1")
    def in_stock(session, request):
        return 12345

    @site.widget("Revenue", permission="reports.view")
    def revenue(session, request):
        return "$9"

    @site.widget("Broken")
    def broken(session, request):
        raise RuntimeError("boom")

    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key="x" * 40)

    root = Panel(TestClient(app), engine)
    root.login()
    page = root.get("/").text
    assert "12,345" in page and 'href="/admin/item?active=1"' in page
    assert "$9" in page
    assert "Unavailable" in page

    viewer = Panel(TestClient(app), engine)
    viewer.login("viewer")
    assert "Revenue" not in viewer.get("/").text


def test_model_routes(engine):
    class StatsAdmin(ModelAdmin):
        def get_routes(self):
            return [("/stats", self.stats, ["GET"])]

        def stats(self, session, request):
            return PlainTextResponse("stats")

    _, panel = mount(engine, (Owner, StatsAdmin))
    assert panel.get("/owner/stats").text == "stats"


def test_template_override(engine, tmp_path):
    folder = tmp_path / "fastapi_panel"
    folder.mkdir()
    (folder / "error.html").write_text("custom error {{ status }}")
    _, panel = mount(engine, (Owner, ModelAdmin), template_dirs=[tmp_path])
    assert panel.get("/owner/9999/change").text == "custom error 404"


def test_form_schema_errors_land_on_the_field(engine):
    class Schema(pydantic.BaseModel):
        name: str = pydantic.Field(min_length=3)

    class SchemaAdmin(ModelAdmin):
        form_schema = Schema
        fields = ("name",)

    _, panel = mount(engine, (Owner, SchemaAdmin))
    page = panel.post("/owner/add", {"name": "ab"})
    assert page.status_code == 200 and "at least 3 characters" in page.text
    assert panel.post("/owner/add", {"name": "abc"}).status_code == 303


def test_field_overrides_and_validators(engine, db):
    def no_digits(value):
        if any(c.isdigit() for c in value):
            raise ValidationError("No digits.")

    class OverrideAdmin(ModelAdmin):
        fields = ("name",)
        field_overrides = {"name": CharField(validators=[no_digits], help_text="Letters only.")}

    _, panel = mount(engine, (Owner, OverrideAdmin))
    assert "Letters only." in panel.get("/owner/add").text
    assert "No digits." in panel.post("/owner/add", {"name": "r2d2"}).text
    assert panel.post("/owner/add", {"name": "Carol"}).status_code == 303
    assert db.scalar(select(Owner).where(Owner.name == "Carol")) is not None


def test_override_does_not_leak_to_another_admin(engine):
    class ChoiceAdmin(ModelAdmin):
        field_overrides = {"name": ChoiceField(choices=[("a", "A")])}

    _, panel = mount(engine, (Owner, ChoiceAdmin))
    assert "<select" in panel.get("/owner/add").text
    _, plain = mount(engine, (Owner, ModelAdmin))
    assert '<select id="id_name"' not in plain.get("/owner/add").text


def test_readonly_method_and_fieldsets(engine, db):
    class DetailAdmin(ModelAdmin):
        readonly_fields = ("shout",)
        fieldsets = (
            ("Main", {"fields": (("name", "price"), "status"), "description": "The basics."}),
            ("More", {"fields": ("shout",), "classes": ("collapse",)}),
        )

        @display("Loud")
        def shout(self, obj):
            return obj.name.upper()

    _, panel = mount(engine, (Item, DetailAdmin))
    item_id = db.scalar(select(Item.id).where(Item.name == "item0"))
    page = panel.get(f"/item/{item_id}/change")
    assert "The basics." in page.text and "ITEM0" in page.text and "<details" in page.text
    # Posting a read-only field must not reach the model.
    panel.post(f"/item/{item_id}/change", {"name": "item0", "price": "1", "status": "DRAFT", "shout": "x"})


def test_stacked_inline_renders_and_saves(engine, db):
    class NoteStacked(StackedInline):
        model = Note

    class ParentAdmin(ModelAdmin):
        inlines = (NoteStacked,)

    _, panel = mount(engine, (Item, ParentAdmin))
    item_id = db.scalar(select(Item.id).where(Item.name == "item0"))
    assert "stacked-row" in panel.get(f"/item/{item_id}/change").text
    panel.post(
        f"/item/{item_id}/change",
        {"name": "item0", "price": "1", "status": "DRAFT", "note-TOTAL": "1", "note-0-text": "stacked"},
    )
    assert db.scalar(select(Note).where(Note.text == "stacked")) is not None


def test_audit_log_can_be_disabled(engine):
    _, panel = mount(engine, (Owner, ModelAdmin), audit_log=False)
    assert panel.post("/owner/add", {"name": "Quiet"}).status_code == 303
    assert panel.get("/owner/1/history").status_code == 404
    assert "recent actions" not in panel.get("/").text


def test_mount_under_another_path(engine):
    site = AdminSite()
    site.register(Owner)
    app = FastAPI()
    site.mount(app, engine=engine, auth=ModelAuth(User), secret_key="x" * 40, path="/backoffice")
    client = TestClient(app)
    assert client.get("/backoffice/login").status_code == 200
    assert "/backoffice/static/panel.css" in client.get("/backoffice/login").text
    assert client.get("/backoffice/static/panel.css").status_code == 200


def test_autodiscover_imports_admin_modules(tmp_path, monkeypatch):
    package = tmp_path / "discover_me"
    (package / "billing").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "billing" / "__init__.py").write_text("")
    (package / "billing" / "admin.py").write_text("LOADED = True\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    autodiscover("discover_me")
    assert sys.modules["discover_me.billing.admin"].LOADED is True


def test_default_site_exists():
    assert isinstance(default_site, AdminSite)


def test_oversized_login_password_is_rejected_quickly(engine):
    _, app = build_client(engine)
    panel = Panel(TestClient(app), engine)
    assert panel.login(password=PASSWORD + "x" * 100_000).status_code == 400


def test_garbage_autocomplete_value_shows_an_error(engine):
    class PickOwnerAdmin(ModelAdmin):
        autocomplete_fields = ("owner",)

    class OwnerSearchAdmin(ModelAdmin):
        search_fields = ("name",)

    _, panel = mount(engine, (Item, PickOwnerAdmin), (Owner, OwnerSearchAdmin))
    page = panel.post("/item/add", {"name": "x", "price": "1", "status": "LIVE", "owner": "abc"})
    assert page.status_code == 200 and "Select a valid choice." in page.text


def test_garbage_value_in_decimal_filter_is_ignored(engine):
    class PriceAdmin(ModelAdmin):
        list_filter = ("price",)

    _, panel = mount(engine, (Item, PriceAdmin))
    assert panel.get("/item?price=abc").status_code == 200
