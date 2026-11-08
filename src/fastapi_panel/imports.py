from __future__ import annotations

import csv
import io
from typing import Any
from urllib.parse import quote

from sqlalchemy.exc import DBAPIError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.responses import Response

from .forms import BooleanField, ManyToManyField
from .introspect import humanize, pk_criterion, pk_keys
from .log import record
from .options import ModelAdmin
from .views import _root, redirect

FALSE_WORDS = {"", "0", "false", "no", "off", "n", "f"}
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
PREVIEW_ROWS = 50


class ImportProblem(Exception):
    pass


class RowData(dict):
    """Looks like submitted form data, so an imported row takes the same path as a saved form."""

    def getlist(self, name: str) -> list[str]:
        return self.get(name) or []


def undo_csv_safe(text: str) -> str:
    return text[1:] if text[:1] == "'" and text[1:2] in FORMULA_START else text


def form_input(field: Any, text: str) -> Any:
    """Turns what an export wrote, like "Yes" or a category's name, back into what the form expects."""
    text = undo_csv_safe(text.strip())
    if isinstance(field, BooleanField):
        return "" if text.lower() in FALSE_WORDS else "on"
    if field.widget == "autocomplete":
        to_value = field.search_key
    else:
        labels = {label.lower(): value for value, label in field.choices}

        def to_value(part: str) -> str:
            return labels.get(part.lower(), part)

    if isinstance(field, ManyToManyField):
        return [to_value(part.strip()) for part in text.split(",") if part.strip()]
    return to_value(text)


def parse_csv(admin: ModelAdmin, raw: bytes) -> list[tuple[int, list[str]]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportProblem("This file isn't UTF-8. Save it as “CSV UTF-8” and try again.") from None
    first_line = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=first_line.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        rows = [(reader.line_num, row) for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise ImportProblem(f"This file can't be read as CSV: {exc}.") from None
    if len(rows) < 2:
        raise ImportProblem("This file has no rows to import. The first line must name the columns.")
    if len(rows) - 1 > admin.import_max_rows:
        raise ImportProblem(f"Import up to {admin.import_max_rows:,} rows at a time.")
    return rows


def match_columns(
    admin: ModelAdmin, request: Any, header: list[str]
) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """Which position holds each field and primary key, by field name or by the label an export uses."""
    wanted = {}
    for name in admin.get_import_fields(request):
        wanted[name.lower()] = name
        wanted[admin.column_label(name).lower()] = name
    keys = {key.lower(): key for key in pk_keys(admin.model)}
    keys.update({humanize(key).lower(): key for key in pk_keys(admin.model)})
    fields: dict[str, int] = {}
    pks: dict[str, int] = {}
    ignored = []
    for position, title in enumerate(header):
        cleaned = title.strip().lower()
        if cleaned in wanted:
            fields[wanted[cleaned]] = position
        elif cleaned in keys:
            pks[keys[cleaned]] = position
        elif cleaned:
            ignored.append(title.strip())
    if not fields:
        raise ImportProblem("None of the columns match. Download the template to see which ones are expected.")
    return fields, pks, ignored


def find_existing(
    session: Any, request: Any, admin: ModelAdmin, pks: dict[str, int], cells: list[str]
) -> tuple[Any, bool]:
    """(the row, whether the CSV named a primary key). Goes through get_queryset, so scoping still applies."""
    keys = pk_keys(admin.model)
    if set(pks) != set(keys):
        return None, False
    parts = [cells[pks[key]].strip() if pks[key] < len(cells) else "" for key in keys]
    if not all(parts):
        return None, False
    criterion = pk_criterion(admin.model, ",".join(quote(part, safe="") for part in parts))
    return session.scalars(admin.get_queryset(request).where(criterion)).first(), True


def import_rows(
    session: Any, request: Any, admin: ModelAdmin, rows: list[tuple[int, list[str]]], *, apply: bool
) -> dict:
    """Runs every row inside one transaction. Previews roll it back, so what they report is what will happen."""
    fields, pks, ignored = match_columns(admin, request, rows[0][1])
    available, _ = admin._available_fields()
    required = [name for name in admin.get_import_fields(request) if getattr(available.get(name), "required", False)]
    can_add = admin.has_add_permission(request)
    results = []
    counts = {"add": 0, "change": 0, "same": 0, "error": 0}

    for line, cells in rows[1:]:
        result = {
            "line": line,
            "status": "error",
            "errors": [],
            "cells": [cells[i] if i < len(cells) else "" for i in fields.values()],
        }
        results.append(result)
        problem = _import_row(session, request, admin, fields, pks, cells, required, can_add, available, result, apply)
        counts[result["status"]] += 1
        if problem:
            # A database error ends the transaction, so the rows after it can't be checked.
            result["errors"].append(problem)
            break

    report = {
        "results": results,
        "counts": counts,
        "columns": [admin.column_label(name) for name in fields],
        "ignored": ignored,
        "is_valid": counts["error"] == 0,
    }
    if apply and report["is_valid"]:
        session.commit()
    else:
        session.rollback()
    return report


def _import_row(session, request, admin, fields, pks, cells, required, can_add, available, result, apply) -> str | None:
    try:
        obj, named = find_existing(session, request, admin, pks, cells)
    except ValueError:
        result["errors"].append("The primary key isn't valid.")
        return None
    if obj is None and named and all(getattr(available.get(key), "generated", True) for key in pks):
        result["errors"].append("No record has this ID, or you can't see it.")
        return None
    if obj is not None and not admin.has_change_permission(request, obj):
        result["errors"].append("You can't change this record.")
        return None
    if obj is None and not can_add:
        result["errors"].append("You can't add records.")
        return None

    present = list(fields)
    names = present if obj is not None else present + [name for name in required if name not in fields]
    target = obj if obj is not None else admin.model()
    form = admin.build_form(session, request, target, persisted=obj is not None, names=names)
    data = RowData()
    for field in form.editable:
        position = fields.get(field.name)
        text = cells[position] if position is not None and position < len(cells) else ""
        data[field.html_name] = form_input(field, text)
    admin.validate_form(session, form, data)
    if not form.is_valid:
        result["errors"] += form.errors + [f"{f.label}: {error}" for f in form.fields for error in f.errors]
        return None

    changed = form.apply(target)
    if obj is not None and not changed:
        result["status"] = "same"
        return None
    try:
        if apply:
            admin.save_model(session, request, target, obj is not None)
        else:
            session.add(target)
        session.flush()
        if apply:
            record(session, request, admin, target, "change" if obj is not None else "add", "Imported from CSV")
    except DBAPIError:
        session.rollback()
        return "The database refused this row, so the rows after it weren't checked."
    result["status"] = "change" if obj is not None else "add"
    return None


def _template_response(request: Any, admin: ModelAdmin) -> Response:
    buffer = io.StringIO()
    csv.writer(buffer).writerow(admin.column_label(name) for name in admin.get_import_fields(request))
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{admin.slug}-template.csv"'},
    )


def import_view(session: Any, request: Any, admin: ModelAdmin) -> Response:
    if not admin.csv_import:
        raise HTTPException(404, "Not found.")
    admin.require(admin.can_import(request))
    if request.query_params.get("template"):
        return _template_response(request, admin)

    report = None
    error = ""
    text = ""
    if request.method == "POST":
        data = request.state.form
        upload = data.get("file")
        if isinstance(upload, UploadFile) and upload.filename:
            raw = upload.file.read()
        else:
            raw = str(data.get("csv_text", "")).encode()
        try:
            if not raw:
                raise ImportProblem("Choose a CSV file first.")
            rows = parse_csv(admin, raw)
            text = raw.decode("utf-8-sig")
            report = import_rows(session, request, admin, rows, apply="_confirm" in data)
        except ImportProblem as exc:
            error = str(exc)
        if report and report["is_valid"] and "_confirm" in data:
            counts = report["counts"]
            admin.message_user(
                request, f"Imported: {counts['add']} added, {counts['change']} changed, {counts['same']} unchanged."
            )
            return redirect(f"{_root(request)}/{admin.slug}")

    preview = []
    if report:
        ordered = sorted(report["results"], key=lambda row: row["status"] != "error")
        preview = ordered[:PREVIEW_ROWS]
    list_url = f"{_root(request)}/{admin.slug}"
    title = f"Import {admin.name_plural.lower()}"
    return admin.site.render(
        request,
        "fastapi_panel/import.html",
        {
            "admin": admin,
            "title": title,
            "crumbs": [("Home", f"{_root(request)}/"), (admin.name_plural, list_url), ("Import", None)],
            "error": error,
            "report": report,
            "preview": preview,
            "hidden_rows": len(report["results"]) - len(preview) if report else 0,
            "csv_text": text,
            "post_url": request.url.path,
            "list_url": list_url,
            "columns": [admin.column_label(name) for name in admin.get_import_fields(request)],
            "pk_label": humanize(pk_keys(admin.model)[0]),
        },
    )
