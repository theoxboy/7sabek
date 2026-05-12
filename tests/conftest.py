from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import get_db
from app.core.config import get_settings
from app.main import create_app


@pytest.fixture(scope="session")
def database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set for integration tests")
    normalized = database_url.lower()
    # Safety guard: never allow running test truncation against non-test databases.
    if "/floussy_test" not in normalized and "/test" not in normalized:
        raise RuntimeError(
            "Refusing to run tests on a non-test DATABASE_URL. "
            "Use a dedicated test database (e.g. .../floussy_test)."
        )
    return database_url


def _asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session", autouse=True)
def apply_migrations(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture(autouse=True)
def clean_db(database_url: str) -> None:
    async def _truncate() -> None:
        conn = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
            tables = [
                row["tablename"]
                for row in rows
                if row["tablename"] != "alembic_version"
            ]
            if tables:
                quoted = ", ".join(f'"{table}"' for table in tables)
                await conn.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_truncate())


@pytest.fixture()
def app(database_url: str):
    os.environ["ENVIRONMENT"] = "test"
    get_settings.cache_clear()
    app = create_app()

    async def override_get_db():
        engine = create_async_engine(database_url, poolclass=NullPool)
        sessionmaker = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False
        )
        async with sessionmaker() as session:
            yield session
        await engine.dispose()

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)
