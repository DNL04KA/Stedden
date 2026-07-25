"""Shared test fixtures: the migrated `stedden_test` database.

Tests that need PostgreSQL depend on ``migrated_test_db``; everything else runs
fully offline.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DB = "stedden_test"
TEST_DB_URL = f"postgresql+psycopg://localhost:5432/{TEST_DB}"

postgres_available = subprocess.run(["pg_isready", "-q"], check=False).returncode == 0

TRUNCATE_SQL = (
    "TRUNCATE action_log, task, appointment, customer_note, customer, "
    "staff_hours, staff, business_hours, notification, lead, "
    "transcript_message, call, policy, faq, service, business CASCADE"
)


@pytest.fixture(scope="session")
def migrated_test_db() -> str:
    if not postgres_available:
        pytest.skip("PostgreSQL is not running")
    subprocess.run(["createdb", TEST_DB], check=False, capture_output=True)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": TEST_DB_URL},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"
    return TEST_DB_URL


@pytest.fixture
def sync_sm(migrated_test_db: str):
    """Sessionmaker for sync tests (e.g. FastAPI TestClient, which owns its own
    event loop). NullPool keeps connections from being shared across loops."""
    engine = create_async_engine(migrated_test_db, poolclass=NullPool)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def prepare() -> None:
        from seed.seed_db import ensure_seeded

        async with engine.begin() as conn:
            await conn.execute(text(TRUNCATE_SQL))
        await ensure_seeded(maker)

    asyncio.run(prepare())
    yield maker
    asyncio.run(engine.dispose())
