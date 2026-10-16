"""Async database engine, session factory and the request-scoped session dependencies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def engine_options(database_url: str) -> tuple[URL, dict[str, Any]]:
    """The URL to connect with and the connection arguments it needs.

    A URL that says `pgbouncer=true` goes through a transaction-mode pooler,
    such as Supabase's on port 6543. There a "session" is handed a different
    server connection for every transaction, so a prepared statement made in
    one may not exist in the next. The driver's statement caches are switched
    off, and each statement it does prepare gets a name of its own.
    """
    url = make_url(database_url)
    query = dict(url.query)
    pooled = str(query.pop("pgbouncer", "")).lower() == "true"
    url = url.set(query=query)
    if not pooled:
        return url, {}
    return url, {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
    }


def create_engine(database_url: str) -> AsyncEngine:
    """Build the engine. No connection is opened until the first query."""
    url, connect_args = engine_options(database_url)
    return create_async_engine(
        url,
        connect_args=connect_args,
        # Hosted poolers can drop an idle connection while a free service sleeps. Check
        # before reuse and recycle regularly, so the first evening request still works.
        # Five connections at most per process keeps three processes within a small pool.
        pool_recycle=240,
        pool_pre_ping=True,
        pool_size=4,
        max_overflow=1,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed when the request ends."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


async def get_transaction(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session in a transaction that commits when the request
    succeeds and rolls back if it raises."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory.begin() as session:
        yield session
