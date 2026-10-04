import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.schemas import WeatherResponse
from app.weather import WeatherService, WeatherServiceError


SAMPLE_GEOCODING = {
    "results": [{"name": "London", "latitude": 51.5, "longitude": -0.12}]
}
SAMPLE_FORECAST = {
    "current": {
        "temperature_2m": 18.5,
        "apparent_temperature": 17.9,
        "relative_humidity_2m": 62,
        "weather_code": 61,
        "time": "2024-10-03T12:00",
    }
}


class MemoryCache:
    def __init__(self, value=None):
        self.value = value
        self.writes = []

    async def get(self, key, request_id=None):
        return self.value

    async def set(self, key, value, request_id=None):
        self.writes.append((key, value))


class MemoryRepository:
    def __init__(self):
        self.records = []

    async def record(self, location, response, cache_hit, request_id=None):
        self.records.append((location, response, cache_hit))


def service_for(handler, cache=None, retry_attempts=3):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = MemoryRepository()
    service = WeatherService(client, cache or MemoryCache(), repository, retry_attempts)
    return service, client, repository


@pytest.mark.asyncio
async def test_fetches_weather_caches_and_records_response():
    seen_requests = []

    def handler(request):
        seen_requests.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=SAMPLE_GEOCODING)
        return httpx.Response(200, json=SAMPLE_FORECAST)

    service, client, repository = service_for(handler)
    try:
        response, cache_hit = await service.get_weather(" London ")
    finally:
        await client.aclose()

    assert response.location == "London"
    assert response.temperature_c == 18.5
    assert response.description == "slight rain"
    assert cache_hit is False
    assert len(seen_requests) == 2
    assert seen_requests[0].url.path == "/v1/search"
    assert seen_requests[1].url.path == "/v1/forecast"
    assert "latitude=51.5" in str(seen_requests[1].url)
    assert repository.records[0][0] == "London"
    assert repository.records[0][2] is False


@pytest.mark.asyncio
async def test_uses_cached_response_without_calling_upstream():
    cached = WeatherResponse(
        location="London",
        temperature_c=18.5,
        feels_like_c=17.9,
        humidity_percent=62,
        description="light rain",
        observed_at=datetime(2024, 10, 3, tzinfo=timezone.utc),
    ).model_dump(mode="json")

    def handler(request):
        raise AssertionError(f"Unexpected upstream call: {request.url}")

    cache = MemoryCache(value=cached)
    service, client, repository = service_for(handler, cache)
    try:
        response, cache_hit = await service.get_weather("London")
    finally:
        await client.aclose()

    assert response.location == "London"
    assert cache_hit is True
    assert cache.writes == []
    assert repository.records[0][2] is True


@pytest.mark.asyncio
async def test_ignores_invalid_cached_payload_and_refetches():
    cache = MemoryCache(value={"location": "London"})

    def handler(request):
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=SAMPLE_GEOCODING)
        return httpx.Response(200, json=SAMPLE_FORECAST)

    service, client, _ = service_for(handler, cache)
    try:
        response, cache_hit = await service.get_weather("London")
    finally:
        await client.aclose()

    assert response.location == "London"
    assert cache_hit is False
    assert len(cache.writes) == 1


@pytest.mark.asyncio
async def test_maps_unknown_location_to_not_found():
    def handler(request):
        return httpx.Response(404, json={"message": "city not found"})

    service, client, _ = service_for(handler)
    try:
        with pytest.raises(WeatherServiceError) as error:
            await service.get_weather("Nowhere")
    finally:
        await client.aclose()

    assert error.value.status_code == 404


@pytest.mark.parametrize("payload", [[], "unexpected JSON shape"])
@pytest.mark.asyncio
async def test_maps_non_object_upstream_json_to_bad_gateway(payload):
    def handler(request):
        return httpx.Response(200, json=payload)

    service, client, _ = service_for(handler)
    try:
        with pytest.raises(WeatherServiceError) as error:
            await service.get_weather("London")
    finally:
        await client.aclose()

    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_maps_upstream_timeout_to_gateway_timeout():
    def handler(request):
        raise httpx.ReadTimeout("provider timed out", request=request)

    service, client, _ = service_for(handler)
    try:
        with pytest.raises(WeatherServiceError) as error:
            await service.get_weather("London")
    finally:
        await client.aclose()

    assert error.value.status_code == 504


@pytest.mark.asyncio
async def test_retries_transient_server_error_then_returns_weather():
    forecast_attempts = 0

    def handler(request):
        nonlocal forecast_attempts
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=SAMPLE_GEOCODING)
        forecast_attempts += 1
        if forecast_attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=SAMPLE_FORECAST)

    service, client, _ = service_for(handler)
    try:
        response, _ = await service.get_weather("London")
    finally:
        await client.aclose()

    assert response.temperature_c == 18.5
    assert forecast_attempts == 2


@pytest.mark.asyncio
async def test_does_not_retry_client_error():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(400)

    service, client, _ = service_for(handler)
    try:
        with pytest.raises(WeatherServiceError) as error:
            await service.get_weather("London")
    finally:
        await client.aclose()

    assert error.value.status_code == 502
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_does_not_retry_non_transient_server_error():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(501)

    service, client, _ = service_for(handler)
    try:
        with pytest.raises(WeatherServiceError) as error:
            await service.get_weather("London")
    finally:
        await client.aclose()

    assert error.value.status_code == 502
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_maps_total_upstream_deadline_to_gateway_timeout():
    async def handler(_request):
        await asyncio.sleep(0.02)
        return httpx.Response(200, json=SAMPLE_GEOCODING)

    service, client, _ = service_for(handler)
    service.total_timeout_seconds = 0.001
    try:
        with pytest.raises(WeatherServiceError) as error:
            await service.get_weather("London")
    finally:
        await client.aclose()

    assert error.value.status_code == 504


@pytest.mark.asyncio
async def test_limits_concurrent_upstream_weather_lookups():
    active_requests = 0
    peak_requests = 0

    async def handler(request):
        nonlocal active_requests, peak_requests
        active_requests += 1
        peak_requests = max(peak_requests, active_requests)
        await asyncio.sleep(0.005)
        active_requests -= 1
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=SAMPLE_GEOCODING)
        return httpx.Response(200, json=SAMPLE_FORECAST)

    service, client, _ = service_for(handler)
    service.upstream_semaphore = asyncio.Semaphore(2)
    try:
        await asyncio.gather(*(service.get_weather(f"City {index}") for index in range(5)))
    finally:
        await client.aclose()

    assert peak_requests == 2