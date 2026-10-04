import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.schemas import WeatherResponse
from app.weather import WeatherServiceError


class FakeWeatherService:
    async def get_weather(self, location, request_id=None):
        return (
            WeatherResponse(
                location=location,
                temperature_c=20,
                feels_like_c=20,
                humidity_percent=50,
                description="clear sky",
                observed_at="2026-10-04T12:00:00Z",
            ),
            False,
        )


@pytest.mark.asyncio
async def test_weather_endpoint_and_prometheus_metrics():
    app = create_app(Settings())
    app.state.weather_service = FakeWeatherService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        weather = await client.get("/weather/Paris")
        metrics = await client.get("/metrics")

    assert weather.status_code == 200
    assert weather.json()["location"] == "Paris"
    assert weather.headers["x-request-id"]
    assert "/weather/{city}" in app.openapi()["paths"]
    assert metrics.status_code == 200
    assert "weather_service_http_requests_total" in metrics.text


@pytest.mark.asyncio
async def test_request_metrics_normalize_unknown_http_methods():
    app = create_app(Settings())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request("WIDGET", "/health")
        metrics = await client.get("/metrics")

    assert response.status_code == 405
    assert 'method="other",route="/health",status="405"' in metrics.text
    assert 'method="WIDGET"' not in metrics.text


@pytest.mark.asyncio
async def test_weather_failure_log_includes_request_id(monkeypatch):
    class FailedWeatherService:
        async def get_weather(self, location, request_id=None):
            raise WeatherServiceError(502, "upstream unavailable")

    log_context = {}

    def capture_warning(_message, **kwargs):
        log_context.update(kwargs.get("extra", {}))

    monkeypatch.setattr("app.main.logger.warning", capture_warning)
    app = create_app(Settings())
    app.state.weather_service = FailedWeatherService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/weather/Paris", headers={"X-Request-ID": "weather-test-id"})

    assert response.status_code == 502
    assert log_context["request_id"] == "weather-test-id"
    assert log_context["path"] == "/weather/Paris"


@pytest.mark.asyncio
async def test_homepage_has_city_entry_form():
    app = create_app(Settings())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="city"' in response.text
    assert 'fetch(`/weather/${encodeURIComponent(city)}`)' in response.text


@pytest.mark.asyncio
async def test_health_is_liveness_and_ready_reports_missing_dependencies():
    app = create_app(Settings())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 503
    assert ready.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_ready_reports_response_logging_failure(monkeypatch):
    class ReadyCache:
        async def ping(self, request_id=None):
            return True

    class ReadyRepository:
        last_write_succeeded = False

    async def database_available(_sessions, request_id=None):
        return True

    monkeypatch.setattr("app.main.check_database", database_available)
    app = create_app(Settings())
    app.state.sessions = object()
    app.state.cache = ReadyCache()
    app.state.weather_repository = ReadyRepository()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ready = await client.get("/ready")

    assert ready.status_code == 503
    assert ready.json()["checks"] == {
        "database": "ok",
        "database_logging": "unavailable",
        "cache": "ok",
    }