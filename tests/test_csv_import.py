from __future__ import annotations

import io

import pytest
from conftest import SECRET, Item, ItemAdmin, Panel, Status, User
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from fastapi_panel import AdminSite, ModelAuth
from fastapi_panel.log import AdminLog
from fastapi_panel.options import csv_safe


class ImportingAdmin(ItemAdmin):
    csv_import = True
    import_max_rows = 5
    list_editable = ()


def mount(engine, admin_class=ImportingAdmin):
    site = AdminSite()
    site.register(Item, admin_class)
    app = FastAPI()
    auth = ModelAuth(User, superuser_field="is_superuser", permissions_field="permissions")
    site.mount(app, engine=engine, auth=auth, secret_key=SECRET)
    return Panel(TestClient(app), engine)


@pytest.fixture
def importer(engine):
    panel = mount(engine)
    panel.login()
    return panel


def upload(panel, text, url="/item/import"):
    body = text if isinstance(text, bytes) else text.encode()
    return panel.post(url, files={"file": ("items.csv", io.BytesIO(body), "text/csv")})


def confirm(panel, text):
    return panel.post("/item/import", {"csv_text": text, "_confirm": "1"})


def items(engine, name):
    with Session(engine) as session:
        return session.scalars(select(Item).where(Item.name == name)).first()


GOOD = 'Name,Price,Active,Status,Owner,Tags\nfresh,9.50,True,Live,Alice,"red, blue"\n'


def test_list_shows_the_button_only_when_enabled(importer, panel):
    assert 'href="/admin/item/import"' in importer.get("/item").text
    assert "/admin/item/import" not in panel.get("/item").text
    assert panel.get("/item/import").status_code == 404


def test_preview_saves_nothing_and_shows_what_will_happen(importer, engine):
    page = upload(importer, GOOD)
    assert page.status_code == 200
    assert "1 new" in page.text and "Nothing has been saved yet" in page.text
    assert items(engine, "fresh") is None


def test_confirm_imports_and_maps_labels_back_to_values(importer, engine):
    response = confirm(importer, GOOD)
    assert response.status_code == 303
    with Session(engine) as session:
        item = session.scalars(select(Item).where(Item.name == "fresh")).one()
        assert str(item.price) == "9.50" and item.active is True and item.status is Status.LIVE
        assert item.owner.name == "Alice"
        assert {tag.name for tag in item.tags} == {"red", "blue"}
        entry = session.scalars(select(AdminLog).order_by(AdminLog.id.desc())).first()
        assert entry.action == "add" and entry.message == "Imported from CSV"


def test_false_words_turn_a_boolean_off(importer, engine):
    confirm(importer, "Name,Active\noff-item,False\n")
    assert items(engine, "off-item").active is False


def test_missing_optional_columns_keep_their_defaults(importer, engine):
    confirm(importer, "Name\nminimal\n")
    item = items(engine, "minimal")
    assert item.active is True and str(item.price) == "1.00"


def test_an_id_column_updates_only_the_columns_given(importer, engine):
    with Session(engine) as session:
        item = session.scalars(select(Item).where(Item.name == "item1")).one()
        pk, body_before = item.id, item.price
    assert upload(importer, f"ID,Name\n{pk},renamed\n").text.count("1 changed") == 1
    confirm(importer, f"ID,Name\n{pk},renamed\n")
    with Session(engine) as session:
        item = session.get(Item, pk)
        assert item.name == "renamed" and item.price == body_before


def test_unchanged_rows_are_reported_and_not_logged(importer, engine):
    with Session(engine) as session:
        pk = session.scalars(select(Item.id).where(Item.name == "item1")).one()
        before = session.scalars(select(AdminLog)).all()
    assert "1 unchanged" in upload(importer, f"ID,Name\n{pk},item1\n").text
    confirm(importer, f"ID,Name\n{pk},item1\n")
    with Session(engine) as session:
        assert len(session.scalars(select(AdminLog)).all()) == len(before)


def test_errors_block_the_whole_file(importer, engine):
    text = "Name,Price\nvalid-row,5\nbad-row,not a number\n"
    page = upload(importer, text)
    assert "Enter a number" in page.text and "1 with errors" in page.text
    assert 'name="_confirm"' not in page.text
    confirm(importer, text)
    assert items(engine, "valid-row") is None


def test_required_column_missing_is_an_error_per_row(importer):
    page = upload(importer, "Price\n5\n")
    assert "Name: This field is required" in page.text


def test_duplicates_inside_the_file_are_caught(importer, engine):
    page = upload(importer, "Name\ntwin\ntwin\n")
    assert "must be unique" in page.text
    assert items(engine, "twin") is None


def test_validation_hook_runs(importer):
    assert "Not this name" in upload(importer, "Name\nforbidden\n").text


def test_unknown_id_is_an_error(importer):
    assert "No record has this ID" in upload(importer, "ID,Name\n99999,ghost\n").text
    assert "isn&#39;t valid" in upload(importer, "ID,Name\nabc,ghost\n").text


def test_unknown_columns_are_ignored_and_listed(importer):
    page = upload(importer, "Name,Shout\nloud,LOUD\n")
    assert "Ignored columns: Shout" in page.text and "1 new" in page.text


def test_no_matching_columns(importer):
    assert "None of the columns match" in upload(importer, "Nope\nx\n").text


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"", "Choose a CSV file"),
        (b"Name\n", "no rows"),
        ("Name\n".encode() + b"\xff\xfe", "UTF-8"),
        (b"Name\na\nb\nc\nd\ne\nf\n", "up to 5"),
    ],
)
def test_bad_files_explain_themselves(importer, body, message):
    assert message in upload(importer, body).text


def test_excel_flavoured_csv(importer, engine):
    confirm(importer, "﻿Name;Price\nsemi;3\n".encode("utf-8").decode("utf-8-sig"))
    assert items(engine, "semi") is not None
    assert "1 new" in upload(importer, "﻿Name;Price\nbom;3\n".encode()).text


def test_export_round_trip_undoes_the_formula_guard(importer, engine):
    guarded = csv_safe("=SUM(A1)")
    confirm(importer, f"Name,Body\nformula,{guarded}\n")
    assert items(engine, "formula").body == "=SUM(A1)"


def test_values_are_escaped_in_the_preview(importer):
    page = upload(importer, "Name\n<script>alert(1)</script>\n")
    assert "<script>alert(1)" not in page.text


def test_template_download(importer):
    response = importer.get("/item/import?template=1")
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.startswith("Name,")


def test_permissions(engine):
    panel = mount(engine)
    panel.login("viewer")
    assert panel.get("/item/import").status_code == 403
    assert panel.post("/item/import", {"csv_text": "Name\nx\n", "_confirm": "1"}).status_code == 403


def test_add_only_users_cant_update(engine):
    with Session(engine) as session:
        session.add(
            User(
                username="adder",
                password=session.scalars(select(User.password)).first(),
                permissions=["item.view", "item.add"],
            )
        )
        pk = session.scalars(select(Item.id).where(Item.name == "item1")).one()
        session.commit()
    panel = mount(engine)
    panel.login("adder")
    assert "You can&#39;t change this record" in upload(panel, f"ID,Name\n{pk},nope\n").text
    assert "1 new" in upload(panel, "Name\nallowed\n").text


def test_csrf_is_required(importer):
    assert importer.post("/item/import", {"csv_text": "Name\nx\n", "_confirm": "1"}, csrf=False).status_code == 403


def test_row_scoping_applies(engine):
    class Scoped(ImportingAdmin):
        def get_queryset(self, request):
            return super().get_queryset(request).where(Item.name != "item1")

    panel = mount(engine, Scoped)
    panel.login()
    with Session(engine) as session:
        pk = session.scalars(select(Item.id).where(Item.name == "item1")).one()
    assert "No record has this ID" in upload(panel, f"ID,Name\n{pk},hijack\n").text
