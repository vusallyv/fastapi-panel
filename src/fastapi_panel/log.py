from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .introspect import object_label, pk_string


class Base(DeclarativeBase):
    pass


class AdminLog(Base):
    __tablename__ = "fastapi_panel_log"
    __table_args__ = (
        Index("ix_fastapi_panel_log_object", "model", "object_id"),
        Index("ix_fastapi_panel_log_user", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    )
    user_id: Mapped[str] = mapped_column(String(255))
    user_label: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(255))
    object_label: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(10))
    # Field names only. Values stay out of the log so it never collects sensitive data.
    message: Mapped[str] = mapped_column(Text, default="")


class SavedFilter(Base):
    __tablename__ = "fastapi_panel_saved_filter"
    __table_args__ = (UniqueConstraint("user_id", "model", "name", name="uq_fastapi_panel_saved_filter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    query: Mapped[str] = mapped_column(Text)


class StaffGroup(Base):
    __tablename__ = "fastapi_panel_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)

    members: Mapped[list[StaffGroupMember]] = relationship(cascade="all, delete-orphan")

    def __str__(self) -> str:
        return self.name


class StaffGroupMember(Base):
    __tablename__ = "fastapi_panel_group_member"

    group_id: Mapped[int] = mapped_column(ForeignKey("fastapi_panel_group.id", ondelete="CASCADE"), primary_key=True)
    # The user's primary key as text, because the user table is yours and can have any key type.
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    def __str__(self) -> str:
        return f"User {self.user_id}"


def record(session: Any, request: Any, admin: Any, obj: Any, action: str, message: str = "") -> None:
    if not admin.site.audit_log:
        return
    auth = admin.site.auth
    user = request.state.user
    session.add(
        AdminLog(
            user_id=auth.user_id(user)[:255],
            user_label=auth.user_label(user)[:255],
            model=admin.slug,
            object_id=pk_string(obj)[:255],
            object_label=object_label(obj)[:255],
            action=action,
            message=message,
        )
    )
