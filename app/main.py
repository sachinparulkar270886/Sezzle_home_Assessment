import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cache import WeatherCache
from app.config import Settings, settings
from app.database import Base, WeatherRepository, check_database
from app.observability import (
    AUTH_REQUESTS,
    RATE_LIMIT_REQUESTS,
    TLS_REQUESTS,
    configure_logging,
    install_request_metrics,
    instrument_database,
    metrics_response,
)
from app.schemas import HealthResponse, WeatherResponse
from app.weather import WeatherService, WeatherServiceError

logger = logging.getLogger(__name__)


def create_app(config: Settings = settings) -> FastAPI:
    configure_logging(config.log_level)
    rate_limit_windows: dict[str, list[float]] = defaultdict(list)

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or request.client.host if request.client else "unknown"
        if request.client:
            return request.client.host
        return "unknown"

    def is_rate_limited(request: Request) -> bool:
        limit = config.rate_limit_per_minute
        if limit <= 0:
            return False
        key = client_ip(request)
        now = time.monotonic()
        window = rate_limit_windows[key]
        window[:] = [stamp for stamp in window if now - stamp < 60]
        if len(window) >= limit:
            return True
        window.append(now)
        return False

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if not config.database_url:
            raise RuntimeError("DATABASE_URL must be configured")
        engine = create_async_engine(config.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        instrument_database(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        redis_client = Redis.from_url(config.redis_url, decode_responses=True)
        timeout = httpx.Timeout(
            connect=config.upstream_connect_timeout_seconds,
            read=config.upstream_read_timeout_seconds,
            write=config.upstream_read_timeout_seconds,
            pool=config.upstream_pool_timeout_seconds,
        )
        http_client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        application.state.engine = engine
        application.state.sessions = sessions
        application.state.redis = redis_client
        application.state.cache = WeatherCache(redis_client, config.cache_ttl_seconds)
        repository = WeatherRepository(sessions)
        application.state.weather_repository = repository
        application.state.weather_service = WeatherService(
            http_client,
            application.state.cache,
            repository,
            config.upstream_retry_attempts,
            config.upstream_total_timeout_seconds,
            config.upstream_max_concurrency,
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await repository.check_write_access()
            yield
        finally:
            await http_client.aclose()
            await redis_client.aclose()
            await engine.dispose()

    application = FastAPI(title=config.app_name, version=config.app_version, lifespan=lifespan)
    install_request_metrics(application)

    @application.middleware("http")
    async def gateway_security(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", "")[:128] or str(uuid.uuid4())
        request.state.request_id = request_id

        forwarded_scheme = request.headers.get("x-forwarded-proto", "")
        scheme = forwarded_scheme.split(",", 1)[0].strip().lower() if forwarded_scheme else request.url.scheme
        TLS_REQUESTS.labels(scheme=scheme, outcome="observed").inc()

        if request.url.path in {"/health", "/ready", "/metrics"}:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

        if config.tls_required and scheme != "https":
            TLS_REQUESTS.labels(scheme=scheme, outcome="blocked").inc()
            return JSONResponse(
                status_code=403,
                content={"detail": "HTTPS/TLS is required for this service", "request_id": request_id},
            )

        if config.require_auth:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.split(" ", 1)[1] if " " in auth_header else ""
            if auth_header.lower().startswith("bearer ") and token == config.auth_token:
                AUTH_REQUESTS.labels(outcome="allowed").inc()
            else:
                AUTH_REQUESTS.labels(outcome="denied").inc()
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized", "request_id": request_id},
                )
        else:
            AUTH_REQUESTS.labels(outcome="disabled").inc()

        if is_rate_limited(request):
            RATE_LIMIT_REQUESTS.labels(outcome="blocked").inc()
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "request_id": request_id},
            )
        RATE_LIMIT_REQUESTS.labels(outcome="allowed").inc()

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(WeatherServiceError)
    async def weather_error_handler(request: Request, exc: WeatherServiceError) -> JSONResponse:
        logger.warning(
            "Weather request failed",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "request_id": getattr(request.state, "request_id", None)},
        )

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home() -> HTMLResponse:
        return HTMLResponse(
            """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Sezzle Weather</title>
    <style>
        :root { color-scheme: light; font-family: Georgia, "Times New Roman", serif; color: #172522; background: #f4f2e9; }
        body { margin: 0; min-height: 100vh; background: linear-gradient(135deg, #f4f2e9 0%, #e2eee8 100%); }
        main { max-width: 680px; margin: 0 auto; padding: 12vh 24px 48px; }
        h1 { margin: 0 0 10px; font-size: 42px; font-weight: 500; }
        p { color: #53645e; font: 16px/1.5 system-ui, sans-serif; }
        form { display: flex; gap: 10px; margin-top: 30px; }
        input { min-width: 0; flex: 1; padding: 14px 16px; border: 1px solid #aabbb2; border-radius: 4px; background: #fff; font: 16px system-ui, sans-serif; }
        button { padding: 0 20px; border: 0; border-radius: 4px; background: #17634d; color: #fff; font: 600 15px system-ui, sans-serif; cursor: pointer; }
        button:disabled { opacity: .65; cursor: wait; }
        #result { margin-top: 26px; padding: 20px; border-left: 3px solid #dc7654; background: rgba(255,255,255,.7); white-space: pre-line; font: 16px/1.6 system-ui, sans-serif; }
        #result:empty { display: none; }
        @media (max-width: 480px) { main { padding-top: 9vh; } form { flex-direction: column; } button { min-height: 48px; } }
    </style>
</head>
<body>
    <main>
        <h1>Weather, where you are.</h1>
        <p>Enter a city to see its current conditions.</p>
        <form id="weather-form">
            <label for="city" hidden>City name</label>
            <input id="city" name="city" placeholder="e.g. London" maxlength="120" required autocomplete="off">
            <button type="submit">Check weather</button>
        </form>
        <section id="result" aria-live="polite" role="status"></section>
    </main>
    <script>
        const form = document.querySelector("#weather-form");
        const cityInput = document.querySelector("#city");
        const result = document.querySelector("#result");
        const button = form.querySelector("button");
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const city = cityInput.value.trim();
            if (!city) return;
            button.disabled = true;
            result.textContent = "Checking weather...";
            try {
                const response = await fetch(`/weather/${encodeURIComponent(city)}`);
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || "Weather lookup failed.");
                result.textContent = `${data.location}\n${data.temperature_c} °C (feels like ${data.feels_like_c} °C)\nHumidity: ${data.humidity_percent}%\n${data.description}`;
            } catch (error) {
                result.textContent = error.message || "Could not retrieve weather.";
            } finally {
                button.disabled = false;
            }
        });
    </script>
</body>
</html>"""
            )

    @application.get("/health", tags=["operations"])
    async def health() -> JSONResponse:
        return JSONResponse(status_code=200, content={"status": "ok"})

    @application.get("/ready", response_model=HealthResponse, tags=["operations"])
    async def ready(request: Request) -> JSONResponse:
        checks: dict[str, str] = {}
        sessions = getattr(request.app.state, "sessions", None)
        cache = getattr(request.app.state, "cache", None)
        repository = getattr(request.app.state, "weather_repository", None)
        request_id = request.state.request_id
        checks["database"] = (
            "ok" if sessions and await check_database(sessions, request_id) else "unavailable"
        )
        checks["database_logging"] = (
            "ok" if repository and repository.last_write_succeeded else "unavailable"
        )
        checks["cache"] = "ok" if cache and await cache.ping(request_id) else "unavailable"
        healthy = all(value == "ok" for value in checks.values())
        status = "ok" if healthy else "degraded"
        return JSONResponse(
            status_code=200 if healthy else 503,
            content=HealthResponse(status=status, checks=checks).model_dump(),
        )

    @application.get("/metrics", include_in_schema=False)
    async def metrics():
        return metrics_response()

    @application.get("/weather/{city}", response_model=WeatherResponse, tags=["weather"])
    async def weather(city: str, request: Request) -> WeatherResponse:
        cleaned = " ".join(city.split())
        if not cleaned or len(cleaned) > 120:
            raise HTTPException(status_code=422, detail="Location must be between 1 and 120 characters")
        response, cache_hit = await request.app.state.weather_service.get_weather(
            cleaned, request.state.request_id
        )
        logger.info(
            "Weather lookup completed",
            extra={"request_id": request.state.request_id, "location": cleaned, "cache_hit": cache_hit},
        )
        return response

    return application


app = create_app()