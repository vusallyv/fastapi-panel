from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, unquote

from sqlalchemy import String, and_, cast, func, inspect
from sqlalchemy.sql.elements import ColumnElement


def humanize(name: str) -> str:
    text = name.replace("__", " ").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def split_camel(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


def pluralize(name: str) -> str:
    if name.endswith("y") and name[-2:-1] not in "aeiou":
        return name[:-1] + "ies"
    if name.endswith(("s", "x", "ch", "sh")):
        return name + "es"
    return name + "s"


def pk_keys(model: type) -> list[str]:
    mapper = inspect(model)
    return [mapper.get_property_by_column(c).key for c in mapper.primary_key]


def pk_string(obj: Any) -> str:
    return ",".join(quote(str(getattr(obj, key)), safe="") for key in pk_keys(type(obj)))


def coerce_to_column(column: Any, raw: str) -> Any:
    """Converts form or URL text to the column's type. Anything that doesn't fit is a ValueError."""
    try:
        return column.type.python_type(raw)
    except (TypeError, ArithmeticError, NotImplementedError) as exc:
        raise ValueError(raw) from exc


def pk_criterion(model: type, raw: str) -> ColumnElement[bool]:
    """Raises ValueError when `raw` can't be a primary key of `model`."""
    keys = pk_keys(model)
    parts = [unquote(part) for part in raw.split(",")]
    if len(parts) != len(keys):
        raise ValueError(raw)
    columns = [getattr(model, key) for key in keys]
    return and_(*[col == coerce_to_column(col, part) for col, part in zip(columns, parts, strict=True)])


def object_label(obj: Any) -> str:
    cls = type(obj)
    if cls.__str__ is not object.__str__ and not cls.__str__.__module__.startswith("pydantic"):
        return str(obj)
    for attr in ("name", "title", "label", "username", "email"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    return f"{split_camel(cls.__name__)} #{pk_string(obj)}"


def resolve_attr(model: type, path: str) -> Any:
    """Returns the attribute at the end of `category__parent__name`, or raises AttributeError."""
    head, _, rest = path.partition("__")
    attr = getattr(model, head)
    return resolve_attr(attr.property.mapper.class_, rest) if rest else attr


def related_criterion(model: type, path: str, build: Callable[[Any], Any]) -> Any:
    head, _, rest = path.partition("__")
    attr = getattr(model, head)
    if not rest:
        return build(attr)
    inner = related_criterion(attr.property.mapper.class_, rest, build)
    return attr.any(inner) if attr.property.uselist else attr.has(inner)


def search_criterion(model: type, spec: str, term: str) -> Any:
    """`spec` follows Django's search_fields: `^` starts with, `=` exact, otherwise contains."""
    mode, path = (spec[0], spec[1:]) if spec[0] in "^=" else ("", spec)

    def build(column: Any) -> Any:
        text = column if isinstance(column.type, String) else cast(column, String)
        if mode == "^":
            return text.istartswith(term, autoescape=True)
        if mode == "=":
            return func.lower(text) == term.lower()
        return text.icontains(term, autoescape=True)

    return related_criterion(model, path, build)


def relationship_paths(model: type, names: list[str]) -> list[str]:
    """The names that traverse a relationship, so the changelist can load them up front."""
    paths = []
    for name in names:
        current, walked = model, []
        for part in name.split("__"):
            prop = inspect(current).relationships.get(part)
            if prop is None:
                break
            walked.append(part)
            current = prop.mapper.class_
        if walked:
            paths.append("__".join(walked))
    return paths
