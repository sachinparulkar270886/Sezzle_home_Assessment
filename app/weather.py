import asyncio
import hashlib
import logging
import time
from typing import Any

import httpx
from pydantic import ValidationError
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from app.observability import CACHE_EVENTS, UPSTREAM_LATENCY, UPSTREAM_REQUESTS
from app.schemas import WeatherResponse

logger = logging.getLogger(__name__)
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})


class _TransientUpstreamError(Exception):
    pass


class WeatherServiceError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class WeatherService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: Any,
        repository: Any,
        retry_attempts: int = 3,
        total_timeout_seconds: float = 20.0,
        max_concurrency: int = 20,
    ) -> None:
        self.client = client
        self.cache = cache
        self.repository = repository
        self.retry_attempts = retry_attempts
        self.total_timeout_seconds = total_timeout_seconds
        self.upstream_semaphore = asyncio.Semaphore(max_concurrency)

    async def get_weather(
        self, location: str, request_id: str | None = None
    ) -> tuple[WeatherResponse, bool]:
        normalized = " ".join(location.split())
        key_digest = hashlib.sha256(normalized.casefold().encode()).hexdigest()
        cache_key = f"weather:v2:{key_digest}"
        cached = await self.cache.get(cache_key, request_id)
        cache_hit = cached is not None

        if cache_hit:
            try:
                response = WeatherResponse.model_validate(cached)
            except ValidationError:
                cache_hit = False
                logger.warning(
                    "Ignoring invalid cached weather response",
                    extra={"location": normalized, "request_id": request_id},
                )
                CACHE_EVENTS.labels("get", "invalid").inc()
        if not cache_hit:
            try:
                async with asyncio.timeout(self.total_timeout_seconds):
                    async with self.upstream_semaphore:
                        response = await self._fetch_weather(normalized, request_id)
            except TimeoutError as exc:
                UPSTREAM_REQUESTS.labels("timeout").inc()
                raise WeatherServiceError(504, "Weather provider request exceeded the time limit") from exc
            await self.cache.set(cache_key, response.model_dump(mode="json"), request_id)

        await self.repository.record(normalized, response, cache_hit, request_id)
        return response, cache_hit

    async def _fetch_weather(self, location: str, request_id: str | None = None) -> WeatherResponse:
        try:
            geocoding = await self._get_json(
                GEOCODING_URL,
                {"name": location, "count": 1, "language": "en", "format": "json"},
            )
            results = geocoding.get("results") or []
            if not results:
                UPSTREAM_REQUESTS.labels("not_found").inc()
                raise WeatherServiceError(404, "Location was not found")
            place = results[0]
            forecast = await self._get_json(
                FORECAST_URL,
                {
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code",
                    "timezone": "UTC",
                },
            )
            return WeatherResponse.from_openmeteo(place["name"], forecast)
        except WeatherServiceError:
            raise
        except _TransientUpstreamError as exc:
            raise WeatherServiceError(502, "Weather provider is temporarily unavailable") from exc
        except httpx.TimeoutException as exc:
            raise WeatherServiceError(504, "Weather provider timed out") from exc
        except httpx.NetworkError as exc:
            raise WeatherServiceError(502, "Could not reach weather provider") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            UPSTREAM_REQUESTS.labels("invalid_response").inc()
            logger.warning(
                "Weather provider returned an invalid response",
                exc_info=True,
                extra={"request_id": request_id},
            )
            raise WeatherServiceError(502, "Weather provider returned an invalid response") from exc

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        retrying = AsyncRetrying(
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, _TransientUpstreamError)),
            wait=wait_random_exponential(multiplier=0.1, max=1.0),
            stop=stop_after_attempt(self.retry_attempts),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                started = time.perf_counter()
                try:
                    try:
                        result = await self.client.get(url, params=params)
                    except httpx.TimeoutException:
                        UPSTREAM_REQUESTS.labels("timeout").inc()
                        raise
                    except httpx.NetworkError:
                        UPSTREAM_REQUESTS.labels("connection_error").inc()
                        raise
                    if result.status_code == 429:
                        UPSTREAM_REQUESTS.labels("rate_limited").inc()
                        raise WeatherServiceError(503, "Weather provider is temporarily rate limited")
                    if result.status_code == 404 and url == GEOCODING_URL:
                        UPSTREAM_REQUESTS.labels("not_found").inc()
                        raise WeatherServiceError(404, "Location was not found")
                    if result.status_code >= 500:
                        UPSTREAM_REQUESTS.labels("server_error").inc()
                        if result.status_code in RETRYABLE_STATUS_CODES:
                            raise _TransientUpstreamError(f"Provider returned {result.status_code}")
                        raise WeatherServiceError(502, "Weather provider returned a non-retryable server error")
                    if result.is_error:
                        UPSTREAM_REQUESTS.labels("client_error").inc()
                        raise WeatherServiceError(502, "Weather provider rejected the request")
                    try:
                        payload = result.json()
                    except ValueError as exc:
                        UPSTREAM_REQUESTS.labels("invalid_response").inc()
                        raise WeatherServiceError(502, "Weather provider returned an invalid response") from exc
                    if not isinstance(payload, dict):
                        UPSTREAM_REQUESTS.labels("invalid_response").inc()
                        raise WeatherServiceError(502, "Weather provider returned an invalid response")
                    UPSTREAM_REQUESTS.labels("success").inc()
                    return payload
                finally:
                    UPSTREAM_LATENCY.observe(time.perf_counter() - started)
        raise RuntimeError("Retry loop completed without a result")