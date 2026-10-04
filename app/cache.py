import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.observability import CACHE_EVENTS

logger = logging.getLogger(__name__)


class WeatherCache:
    def __init__(self, client: Redis, ttl_seconds: int) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds

    async def get(self, key: str, request_id: str | None = None) -> dict[str, Any] | None:
        try:
            value = await self.client.get(key)
            outcome = "hit" if value is not None else "miss"
            CACHE_EVENTS.labels("get", outcome).inc()
            return json.loads(value) if value is not None else None
        except Exception:
            CACHE_EVENTS.labels("get", "error").inc()
            logger.warning("Cache read failed", exc_info=True, extra={"request_id": request_id})
            return None

    async def set(self, key: str, value: dict[str, Any], request_id: str | None = None) -> None:
        try:
            await self.client.set(key, json.dumps(value), ex=self.ttl_seconds)
            CACHE_EVENTS.labels("set", "success").inc()
        except Exception:
            CACHE_EVENTS.labels("set", "error").inc()
            logger.warning("Cache write failed", exc_info=True, extra={"request_id": request_id})

    async def ping(self, request_id: str | None = None) -> bool:
        try:
            healthy = bool(await self.client.ping())
            outcome = "success" if healthy else "error"
            CACHE_EVENTS.labels("ping", outcome).inc()
            if not healthy:
                logger.warning("Cache health check failed", extra={"request_id": request_id})
            return healthy
        except Exception:
            CACHE_EVENTS.labels("ping", "error").inc()
            logger.warning("Cache health check failed", exc_info=True, extra={"request_id": request_id})
            return False