from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy import extract, inspect

from .introspect import coerce_to_column, humanize, object_label

MAX_RELATION_OPTIONS = 50


class Filter:
    """A box in the changelist sidebar. Only registered filters ever read the query string."""

    title = ""
    parameter = ""

    def options(self, session: Any, request: Any) -> list[tuple[str, str]]:
        raise NotImplementedError

    def apply(self, stmt: Any, value: str, request: Any) -> Any:
        raise NotImplementedError


class SimpleFilter(Filter):
    """Subclass this for custom filters: set `title` and `parameter`, define `lookups` and `queryset`."""

    def lookups(self, request: Any) -> list[tuple[str, str]]:
        raise NotImplementedError

    def queryset(self, request: Any, stmt: Any, value: str) -> Any:
        raise NotImplementedError

    def options(self, session: Any, request: Any) -> list[tuple[str, str]]:
        return [(str(v), label) for v, label in self.lookups(request)]

    def apply(self, stmt: Any, value: str, request: Any) -> Any:
        if value not in {v for v, _ in self.options(None, request)}:
            return stmt
        return self.queryset(request, stmt, value)


class _ColumnFilter(Filter):
    def __init__(self, model: type, name: str) -> None:
        self.model = model
        self.name = name
        self.parameter = name
        self.title = humanize(name)
        self.attr = getattr(model, name)


class BooleanFilter(_ColumnFilter):
    def options(self, session: Any, request: Any) -> list[tuple[str, str]]:
        return [("1", "Yes"), ("0", "No")]

    def apply(self, stmt: Any, value: str, request: Any) -> Any:
        if value not in ("0", "1"):
            return stmt
        return stmt.where(self.attr.is_(value == "1"))


class EnumFilter(_ColumnFilter):
    def __init__(self, model: type, name: str) -> None:
        super().__init__(model, name)
        type_ = self.attr.type
        if type_.enum_class:
            self._values = {m.name: m for m in type_.enum_class}
        else:
            self._values = {e: e for e in type_.enums}

    def options(self, session: Any, request: Any) -> list[tuple[str, str]]:
        return [(key, humanize(key.lower())) for key in self._values]

    def apply(self, stmt: Any, value: str, request: Any) -> Any:
        if value not in self._values:
            return stmt
        return stmt.where(self.attr == self._values[value])


class ValuesFilter(_ColumnFilter):
    """Distinct values of a plain column, for low-cardinality text like a country code."""

    def options(self, session: Any, request: Any) -> list[tuple[str, str]]:
        stmt = sa.select(self.attr).where(self.attr.is_not(None)).distinct().order_by(self.attr)
        return [(str(v), str(v)) for v in session.scalars(stmt.limit(MAX_RELATION_OPTIONS))]

    def apply(self, stmt: Any, value: str, request: Any) -> Any:
        try:
            return stmt.where(self.attr == coerce_to_column(self.attr, value))
        except ValueError:
            return stmt


class DateFilter(_ColumnFilter):
    RANGES = {"today": 0, "7d": 7, "30d": 30, "365d": 365}

    def __init__(self, model: type, name: str, site: Any) -> None:
        super().__init__(model, name)
        self.site = site

    def options(self, session: Any, request: Any) -> list[tuple[str, str]]:
        return [("today", "Today"), ("7d", "Past 7 days"), ("30d", "Past 30 days"), ("365d", "Past year")]

    def apply(self, stmt: Any, value: str, request: Any) -> Any:
        if value not in self.RANGES:
            return stmt
        today = self.site.now().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        start = today - dt.timedelta(days=self.RANGES[value])
        if not isinstance(self.attr.type, sa.DateTime):
            return stmt.where(self.attr >= start.date())
        return stmt.where(self.attr >= self.site.from_local(start, bool(self.attr.type.timezone)))


class RelationFilter(_ColumnFilter):
    def __init__(self, model: type, name: str) -> None:
        super().__init__(model, name)
        self.target = self.attr.property.mapper.class_
        self.target_pk = inspect(self.target).primary_key[0]

    def options(self, session: Any, request: Any) -> list[tuple[str, str]]:
        # shortcut: options stop at MAX_RELATION_OPTIONS, a SimpleFilter fits bigger tables better
        key = inspect(self.target).get_property_by_column(self.target_pk).key
        stmt = sa.select(self.target).limit(MAX_RELATION_OPTIONS)
        return [(str(getattr(o, key)), object_label(o)) for o in session.scalars(stmt)]

    def apply(self, stmt: Any, value: str, request: Any) -> Any:
        try:
            pk = coerce_to_column(self.target_pk, value)
        except ValueError:
            return stmt
        match = self.target_pk == pk
        return stmt.where(self.attr.any(match) if self.attr.property.uselist else self.attr.has(match))


def make_filter(model: type, spec: Any, site: Any) -> Filter:
    if isinstance(spec, type) and issubclass(spec, Filter):
        return spec()
    if isinstance(spec, Filter):
        return spec
    mapper = inspect(model)
    if spec in mapper.relationships:
        return RelationFilter(model, spec)
    if spec not in mapper.column_attrs:
        raise AttributeError(f"{model.__name__} has no column or relationship called {spec!r}")
    type_ = getattr(getattr(model, spec).type, "impl_instance", getattr(model, spec).type)
    if isinstance(type_, sa.Boolean):
        return BooleanFilter(model, spec)
    if isinstance(type_, sa.Enum):
        return EnumFilter(model, spec)
    if isinstance(type_, (sa.DateTime, sa.Date)):
        return DateFilter(model, spec, site)
    return ValuesFilter(model, spec)


class DateHierarchy:
    """Year > month > day drill-down above the changelist. Filters on ranges so indexes still apply.

    shortcut: it groups by stored time, not the site timezone, because shifting needs SQL per database.
    """

    def __init__(self, model: type, name: str) -> None:
        self.name = name
        self.attr = getattr(model, name)

    def _selection(self, params: Any) -> dict[str, int]:
        selected: dict[str, int] = {}
        for part in ("year", "month", "day"):
            raw = params.get(f"{self.name}__{part}", "")
            if not raw.isdigit():
                break
            selected[part] = int(raw)
        return selected

    def apply(self, stmt: Any, params: Any) -> Any:
        selected = self._selection(params)
        if not selected:
            return stmt
        try:
            year = selected["year"]
            month, day = selected.get("month", 1), selected.get("day", 1)
            start = dt.datetime(year, month, day)
            if "day" in selected:
                end = start + dt.timedelta(days=1)
            elif "month" in selected:
                end = dt.datetime(year + month // 12, month % 12 + 1, 1)
            else:
                end = dt.datetime(year + 1, 1, 1)
        except (ValueError, OverflowError):
            return stmt
        if getattr(self.attr.type, "timezone", False):
            start, end = start.replace(tzinfo=dt.timezone.utc), end.replace(tzinfo=dt.timezone.utc)
        return stmt.where(self.attr >= start, self.attr < end)

    def links(self, session: Any, stmt: Any, params: Any, url: Any) -> dict[str, Any]:
        selected = self._selection(params)
        parts = ["year", "month", "day"]
        current = parts[min(len(selected), 2)]
        keep: dict[str, Any] = {}
        crumbs = []
        for part, value in selected.items():
            keep[f"{self.name}__{part}"] = value
            crumbs.append({"label": str(value), "url": url(**keep)})
        if len(selected) == 3:
            return {"crumbs": crumbs, "links": []}
        column = extract(current, self.attr)
        values = session.scalars(
            stmt.with_only_columns(column).where(self.attr.is_not(None)).distinct().order_by(None).limit(40)
        )
        links = [
            {"label": str(int(v)), "url": url(**keep, **{f"{self.name}__{current}": int(v)})} for v in sorted(values)
        ]
        clear = url(**{f"{self.name}__{p}": None for p in parts})
        return {"crumbs": crumbs, "links": links, "clear": clear}
