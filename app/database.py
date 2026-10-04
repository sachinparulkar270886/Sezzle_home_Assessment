import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.observability import DB_OPERATION_LATENCY, DB_OPERATIONS
from app.schemas import WeatherResponse

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class WeatherRequest(Base):
    __tablename__ = "weather_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requested_location: Mapped[str] = mapped_column(String(120), index=True)
    response_location: Mapped[str] = mapped_column(String(120))
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WeatherRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions
        self.last_write_succeeded = True

    async def check_write_access(self) -> bool:
        try:
            async with self.sessions() as session:
                transaction = await session.begin()
                try:
                    session.add(
                        WeatherRequest(
                            requested_location="__healthcheck__",
                            response_location="__healthcheck__",
                            response_json={},
                            cache_hit=False,
                        )
                    )
                    await session.flush()
                finally:
                    await transaction.rollback()
            self.last_write_succeeded = True
        except Exception:
            self.last_write_succeeded = False
            logger.warning(
                "Database response logging write check failed",
                exc_info=True,
                extra={"context": "startup"},
            )
        return self.last_write_succeeded

    async def record(
        self,
        requested_location: str,
        response: WeatherResponse,
        cache_hit: bool,
        request_id: str | None = None,
    ) -> None:
        started = time.perf_counter()
        try:
            async with self.sessions() as session:
                session.add(
                    WeatherRequest(
                        requested_location=requested_location,
                        response_location=response.location,
                        response_json=response.model_dump(mode="json"),
                        cache_hit=cache_hit,
                    )
                )
                await session.commit()
            self.last_write_succeeded = True
            DB_OPERATIONS.labels("record_weather", "success").inc()
        except Exception:
            self.last_write_succeeded = False
            DB_OPERATIONS.labels("record_weather", "error").inc()
            logger.warning(
                "Could not persist weather response",
                exc_info=True,
                extra={"request_id": request_id},
            )
        finally:
            DB_OPERATION_LATENCY.labels("record_weather").observe(time.perf_counter() - started)


async def check_database(
    sessions: async_sessionmaker[AsyncSession], request_id: str | None = None
) -> bool:
    try:
        async with sessions() as session:
            await session.execute(select(WeatherRequest.id).limit(1))
        return True
    except Exception:
        logger.warning("Database health check failed", exc_info=True, extra={"request_id": request_id})
        return False