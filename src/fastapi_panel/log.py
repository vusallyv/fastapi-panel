from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
