from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, select

from .introspect import object_label, pk_criterion, pk_string
from .security import DUMMY_HASH, hash_password, verify_password


class AuthBackend:
    """Tells the panel who may log in and what they may do.

    Methods taking `session` run with an open database session. `check_password` runs
    in a worker thread, so slow hashers are safe.
    """

    can_change_password = False

    def find_user(self, session: Any, username: str) -> Any | None:
        raise NotImplementedError

    def get_user(self, session: Any, user_id: str) -> Any | None:
        raise NotImplementedError

    def check_password(self, user: Any | None, password: str) -> bool:
        raise NotImplementedError

    def user_id(self, user: Any) -> str:
        raise NotImplementedError

    def user_label(self, user: Any) -> str:
        return object_label(user)

    def is_allowed(self, user: Any) -> bool:
        return True

    def has_perm(self, user: Any, perm: str) -> bool:
        return True

    def session_hash(self, user: Any) -> str:
        """Sessions die when this value changes. Derive it from the password hash."""
        return ""

    def set_password(self, session: Any, user: Any, password: str) -> None:
        raise NotImplementedError


class ModelAuth(AuthBackend):
    """Log in against one of your own models.

    `active_field` and `staff_field` must exist or be passed as None on purpose: without
    this, every customer in a shared `users` table could log in to the panel.
    Bring your own hasher through `verify` and `hasher` (bcrypt, argon2, passlib...).
    """

    can_change_password = True

    def __init__(
        self,
        model: type,
        *,
        username_field: str = "username",
        password_field: str = "password",
        active_field: str | None = "is_active",
        staff_field: str | None = "is_staff",
        superuser_field: str | None = None,
        permissions_field: str | None = None,
        verify: Callable[[str, str], bool] = verify_password,
        hasher: Callable[[str], str] = hash_password,
    ) -> None:
        columns = inspect(model).attrs
        for name in (username_field, password_field, active_field, staff_field, superuser_field, permissions_field):
            if name is not None and name not in columns:
                raise ValueError(f"{model.__name__} has no field {name!r}. Pass the right name, or None to skip it.")
        self.model = model
        self.username_field = username_field
        self.password_field = password_field
        self.active_field = active_field
        self.staff_field = staff_field
        self.superuser_field = superuser_field
        self.permissions_field = permissions_field
        self.verify = verify
        self.hasher = hasher

    def find_user(self, session: Any, username: str) -> Any | None:
        column = getattr(self.model, self.username_field)
        return session.scalars(select(self.model).where(column == username)).first()

    def get_user(self, session: Any, user_id: str) -> Any | None:
        try:
            return session.scalars(select(self.model).where(pk_criterion(self.model, user_id))).first()
        except ValueError:
            return None

    def check_password(self, user: Any | None, password: str) -> bool:
        if user is None:
            verify_password(password, DUMMY_HASH)
            return False
        stored = getattr(user, self.password_field)
        return bool(stored) and self.verify(password, stored)

    def user_id(self, user: Any) -> str:
        return pk_string(user)

    def user_label(self, user: Any) -> str:
        return str(getattr(user, self.username_field))

    def is_allowed(self, user: Any) -> bool:
        return all(getattr(user, name) for name in (self.active_field, self.staff_field) if name)

    def has_perm(self, user: Any, perm: str) -> bool:
        if self.superuser_field and getattr(user, self.superuser_field):
            return True
        if not self.permissions_field:
            return self.superuser_field is None
        granted = getattr(user, self.permissions_field) or []
        return perm in granted or "*" in granted or f"{perm.split('.')[0]}.*" in granted

    def session_hash(self, user: Any) -> str:
        return hashlib.sha256(str(getattr(user, self.password_field)).encode()).hexdigest()

    def set_password(self, session: Any, user: Any, password: str) -> None:
        setattr(user, self.password_field, self.hasher(password))

    def create_user(self, session: Any, username: str, password: str, *, superuser: bool, **extra: Any) -> Any:
        user = self.model(**extra)
        setattr(user, self.username_field, username)
        self.set_password(session, user, password)
        for name in (self.active_field, self.staff_field):
            if name:
                setattr(user, name, True)
        if self.superuser_field:
            setattr(user, self.superuser_field, superuser)
        session.add(user)
        session.commit()
        return user

    def required_fields(self) -> list[Any]:
        """Other columns a new user can't be saved without, like an email."""
        known = {self.username_field, self.password_field, self.active_field, self.staff_field, self.superuser_field}
        return [
            prop
            for prop in inspect(self.model).column_attrs
            if prop.key not in known
            and not prop.columns[0].nullable
            and not prop.columns[0].primary_key
            and prop.columns[0].default is None
            and prop.columns[0].server_default is None
        ]


@dataclass(frozen=True)
class StaticUser:
    username: str
    password_hash: str

    def __str__(self) -> str:
        return self.username


class StaticAuth(AuthBackend):
    """Users kept in config, no table needed. Hashes come from `python -m fastapi_panel hash-password`."""

    def __init__(self, users: Mapping[str, str]) -> None:
        for username, encoded in users.items():
            if not encoded.startswith("scrypt$"):
                raise ValueError(f"Password for {username!r} must be a hash, not plain text.")
        self.users = {name: StaticUser(name, encoded) for name, encoded in users.items()}

    def find_user(self, session: Any, username: str) -> StaticUser | None:
        return self.users.get(username)

    get_user = find_user

    def check_password(self, user: StaticUser | None, password: str) -> bool:
        if user is None:
            verify_password(password, DUMMY_HASH)
            return False
        return verify_password(password, user.password_hash)

    def user_id(self, user: StaticUser) -> str:
        return user.username

    def session_hash(self, user: StaticUser) -> str:
        return hashlib.sha256(user.password_hash.encode()).hexdigest()
