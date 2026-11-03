from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

T = TypeVar("T")


class Database:
    """Runs plain synchronous code against a sync or async engine.

    Everything in the panel is written once, against a regular Session. Async engines
    go through `run_sync`, which is why lazy loading just works in templates and hooks.
    """

    def __init__(self, bind: Any) -> None:
        if isinstance(bind, AsyncEngine):
            bind = async_sessionmaker(bind, expire_on_commit=False)
        elif isinstance(bind, Engine):
            bind = sessionmaker(bind, expire_on_commit=False)
        if not isinstance(bind, (sessionmaker, async_sessionmaker)):
            raise TypeError("engine must be an Engine, AsyncEngine or a sessionmaker")
        self.maker = bind
        self.is_async = isinstance(bind, async_sessionmaker)

    async def run(self, fn: Callable[..., T], *args: Any) -> T:
        if self.is_async:
            session: AsyncSession
            async with self.maker() as session:
                return await session.run_sync(fn, *args)
        return await run_in_threadpool(self._run_sync, fn, *args)

    def _run_sync(self, fn: Callable[..., T], *args: Any) -> T:
        session: Session
        with self.maker() as session:
            return fn(session, *args)
