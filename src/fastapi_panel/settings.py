from __future__ import annotations

import datetime as dt
import decimal
import json
import logging
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from .forms import (
    SENSITIVE_NAME,
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    DecimalField,
    Field,
    FloatField,
    Form,
    IntegerField,
    JSONField,
    TimeField,
)
from .log import SETTINGS_SLUG, LiveSetting, record
from .options import ImproperlyConfigured
from .views import DB_ERROR, redirect

logger = logging.getLogger("fastapi_panel")
MAX_LOGGED_VALUE = 200


def _default_field(default: Any) -> Field:
    # bool before int and datetime before date, because each is a subclass of the other.
    if isinstance(default, bool):
        return BooleanField()
    if isinstance(default, int):
        return IntegerField()
    if isinstance(default, float):
        return FloatField()
    if isinstance(default, decimal.Decimal):
        return DecimalField()
    if isinstance(default, str):
        field = CharField()
        field.empty_value = ""
        return field
    if isinstance(default, dt.datetime):
        return DateTimeField(aware=default.tzinfo is not None)
    if isinstance(default, dt.date):
        return DateField()
    if isinstance(default, dt.time):
        return TimeField()
    if isinstance(default, (list, dict)):
        return JSONField()
    raise ImproperlyConfigured(f"A setting's default can't be {type(default).__name__}.")


class Setting:
    """One value people can change in the panel. The default's type decides how it's stored and edited."""

    def __init__(
        self,
        default: Any,
        *,
        label: str | None = None,
        help_text: str = "",
        group: str = "General",
        field: Field | None = None,
    ) -> None:
        self.default = default
        inferred = _default_field(default)  # Also refuses a default that can't be stored.
        self.field = inferred if field is None else field
        self.field.label = label or self.field.label
        self.field.help_text = help_text or self.field.help_text
        self.group = group

    def dump(self, value: Any) -> str:
        if isinstance(value, decimal.Decimal):
            value = str(value)
        elif isinstance(value, (dt.date, dt.time)):
            value = value.isoformat()
        return json.dumps(value)

    def load(self, raw: str) -> Any:
        kind = type(self.default)
        value = json.loads(raw)
        if issubclass(kind, (dt.date, dt.time)):
            return kind.fromisoformat(value)
        if issubclass(kind, (list, dict)):
            if not isinstance(value, kind):
                raise TypeError(raw)
            return value
        return kind(value)


class LiveSettings:
    """Read with `await settings.aget("name")`, or `settings.get("name")` on a sync engine."""

    def __init__(self, site: Any, definitions: dict[str, Setting]) -> None:
        for name in definitions:
            if SENSITIVE_NAME.search(name):
                # Stored unencrypted, shown in the form and written to the history.
                raise ImproperlyConfigured(f"Setting {name!r} looks like a secret. Keep secrets out of the database.")
        self.site = site
        self.definitions = definitions

    def _read(self, session: Any, name: str) -> Any:
        setting = self.definitions[name]
        row = session.get(LiveSetting, name)
        return setting.default if row is None else self._decode(setting, row)

    def _decode(self, setting: Setting, row: LiveSetting) -> Any:
        try:
            return setting.load(row.value)
        except (ValueError, TypeError, ArithmeticError):
            # The default's type can change in code after a value was saved.
            logger.warning("Ignoring the saved value of setting %r, it no longer fits.", row.name)
            return setting.default

    def _database(self) -> Any:
        db = getattr(self.site, "db", None)
        if db is None:
            raise RuntimeError("Call site.mount(...) before reading settings.")
        return db

    # shortcut: one query per read, add a short cache if a setting is read many times per request.
    async def aget(self, name: str) -> Any:
        db = self._database()
        self.definitions[name]  # An unknown name is a KeyError, even if the database is unreachable.
        await self.site._ensure_tables()
        return await db.run(self._read, name)

    def get(self, name: str) -> Any:
        db = self._database()
        self.definitions[name]
        if db.is_async:
            raise RuntimeError("This site uses an async engine. Use `await settings.aget(name)`.")
        if not self.site._tables_ready:
            try:
                db._run_sync(self.site._create_tables)
            except DBAPIError:
                pass  # Another worker won the race, or the table is managed by migrations.
            self.site._tables_ready = True
        return db._run_sync(self._read, name)


def _logged(field: Field, value: Any) -> str:
    text = field.describe(value) or "(empty)"
    return text if len(text) <= MAX_LOGGED_VALUE else text[: MAX_LOGGED_VALUE - 1] + "…"


def settings_view(session: Any, request: Any, site: Any) -> Any:
    live = site.settings
    rows = {row.name: row for row in session.scalars(select(LiveSetting))}
    values = SimpleNamespace(
        **{
            name: setting.default if name not in rows else live._decode(setting, rows[name])
            for name, setting in live.definitions.items()
        }
    )
    fields: list[Field] = []
    sections: dict[str, list[list[Field]]] = {}
    for name, setting in live.definitions.items():
        field = setting.field.bind(name)
        field.prepare(session, request, site)
        default = field.describe(field.format(setting.default)) or "empty"
        field.help_text = f"{field.help_text} Default: {default}.".strip()
        fields.append(field)
        sections.setdefault(setting.group, []).append([field])
    form = Form(fields, values, persisted=True)
    form.fieldsets = [
        {"title": title, "description": "", "collapsed": False, "rows": group} for title, group in sections.items()
    ]

    if request.method == "POST":
        form.validate(request.state.form)
        if form.is_valid:
            form.apply(values)
            shim = SimpleNamespace(site=site, slug=SETTINGS_SLUG)
            try:
                for field, before, after in form.diffs:
                    setting = live.definitions[field.name]
                    value = form.cleaned[field.name]
                    row = rows.get(field.name)
                    # Back at the default means no row, so a later change to the default in code applies.
                    if value is None or value == setting.default:
                        if row is not None:
                            session.delete(row)
                    elif row is not None:
                        row.value = setting.dump(value)
                    else:
                        session.add(LiveSetting(name=field.name, value=setting.dump(value)))
                    message = f"{field.label}: {_logged(field, before)} → {_logged(field, after)}"
                    record(session, request, shim, LiveSetting(name=field.name), "change", message)
                session.commit()
            except DBAPIError:
                session.rollback()
                form.errors.append(DB_ERROR)
            else:
                site.message(request, "Settings saved." if form.diffs else "Nothing changed.")
                return redirect(request.url.path)
        if not form.errors:
            form.errors.append("Please fix the errors below.")

    root = request.scope["root_path"]
    return site.render(
        request,
        "fastapi_panel/settings.html",
        {"title": "Settings", "crumbs": [("Home", f"{root}/"), ("Settings", None)], "form": form},
    )
