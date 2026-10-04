import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response


HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"})

REQUESTS = Counter(
    "weather_service_http_requests_total",
    "HTTP requests handled by the service.",
    ("method", "route", "status"),
)
REQUEST_LATENCY = Histogram(
    "weather_service_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ("method", "route"),
)
UPSTREAM_REQUESTS = Counter(
    "weather_service_upstream_requests_total",
    "Requests made to the weather provider.",
    ("outcome",),
)
UPSTREAM_LATENCY = Histogram(
    "weather_service_upstream_request_duration_seconds",
    "Weather provider request latency in seconds.",
)
CACHE_EVENTS = Counter(
    "weather_service_cache_events_total",
    "Cache operations by outcome.",
    ("operation", "outcome"),
)
DB_OPERATIONS = Counter(
    "weather_service_db_operations_total",
    "Database operations by outcome.",
    ("operation", "outcome"),
)
DB_OPERATION_LATENCY = Histogram(
    "weather_service_db_operation_duration_seconds",
    "Database operation latency in seconds.",
    ("operation",),
)
DB_POOL_CHECKED_OUT = Gauge(
    "weather_service_db_pool_checked_out",
    "Number of checked-out database connections.",
)
DB_POOL_CONNECTIONS = Gauge(
    "weather_service_db_pool_connections",
    "Number of open database connections.",
)
DB_POOL_CHECKOUTS = Counter(
    "weather_service_db_pool_checkouts_total",
    "Number of database connection checkouts.",
)
TLS_REQUESTS = Counter(
    "weather_service_tls_requests_total",
    "HTTP requests classified by observed transport security state.",
    ("scheme", "outcome"),
)
AUTH_REQUESTS = Counter(
    "weather_service_auth_requests_total",
    "Gateway authentication outcomes for incoming requests.",
    ("outcome",),
)
RATE_LIMIT_REQUESTS = Counter(
    "weather_service_rate_limit_total",
    "Rate limiting outcomes for incoming requests.",
    ("outcome",),
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status_code", "location", "cache_hit"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def instrument_database(engine: Any) -> None:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        del dbapi_connection, connection_record
        DB_POOL_CONNECTIONS.inc()

    @event.listens_for(engine.sync_engine, "close")
    def on_close(dbapi_connection: Any, connection_record: Any) -> None:
        del dbapi_connection, connection_record
        DB_POOL_CONNECTIONS.dec()

    @event.listens_for(engine.sync_engine, "checkout")
    def on_checkout(dbapi_connection: Any, connection_record: Any, connection_proxy: Any) -> None:
        DB_POOL_CHECKED_OUT.inc()
        DB_POOL_CHECKOUTS.inc()

    @event.listens_for(engine.sync_engine, "checkin")
    def on_checkin(dbapi_connection: Any, connection_record: Any) -> None:
        DB_POOL_CHECKED_OUT.dec()

def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def install_request_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe_requests(request: Request, call_next: Any) -> Response:
        started = time.perf_counter()
        response: Response
        try:
            response = await call_next(request)
        except Exception:
            response = Response(status_code=500)
            raise
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            status_code = getattr(locals().get("response"), "status_code", 500)
            method = request.method if request.method in HTTP_METHODS else "other"
            REQUESTS.labels(method, route, str(status_code)).inc()
            REQUEST_LATENCY.labels(method, route).observe(time.perf_counter() - started)
        return response