from __future__ import annotations

from sqlalchemy import BigInteger, Column, Float, Integer, String
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

import config

Base = declarative_base()


class OpenAILog(Base):
    """OpenAI API call log stored in SQLite (or any SQLAlchemy async DB)."""

    __tablename__ = "openai_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    request_url = Column(String)
    request_method = Column(String)
    # Request start timestamp in milliseconds since Unix epoch.
    request_time = Column(BigInteger)
    # Response duration in seconds (float). Column name kept for backwards compatibility.
    response_duration = Column(Float)
    status_code = Column(Integer)
    request_content = Column(String)
    response_header = Column(String)
    response_content = Column(String)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "request_url": self.request_url,
            "request_method": self.request_method,
            "request_time": self.request_time,
            "response_duration": self.response_duration,
            "status_code": self.status_code,
            "request_content": self.request_content,
            "response_header": self.response_header,
            "response_content": self.response_content,
        }


DATABASE_URL = config.DATABASE_URL
engine: AsyncEngine = create_async_engine(DATABASE_URL)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def configure_database(database_url: str) -> None:
    """Rebind module engine/sessionmaker to a different database URL (useful in tests)."""
    global DATABASE_URL, engine, SessionLocal
    DATABASE_URL = database_url
    engine = create_async_engine(database_url)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_tables() -> None:
    async with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    await engine.dispose()


async def save_log(log_entry: OpenAILog) -> None:
    async with SessionLocal() as session:
        session.add(log_entry)
        await session.commit()
