# Sezzle Weather Service

A small, production-minded FastAPI service that fetches current weather from Open-Meteo, caches successful results in Redis, records successful responses in MySQL, and exposes Prometheus metrics. Open-Meteo requires no API key. The assessment PDF is included in this repository for reviewer context.

## Run locally with Docker Compose

Requirements: Python and Docker Desktop with the Compose plugin. No weather API key or account is required.

```sh
cp .env.example .env
docker compose up --build
```

Or run the setup script, which creates or reuses `.venv`, installs the Python dependencies there, and starts Docker Compose:

```sh
python3 setup.py
```

Preview the actions without making changes with `python3 setup.py --dry-run`.



The sample `.env` uses `sezzle` for both MySQL passwords for local evaluation only. Replace these with distinct strong values before using the service outside a local development environment.

The service is available at `http://localhost:8000`; Prometheus is available at `http://localhost:9090`. Open the weather address in a browser, enter a city name, and select **Check weather** to see current conditions. Compose binds both interfaces to loopback only, so they are not exposed to other hosts on the network. Compose starts MySQL and Redis and waits for their health checks before starting the API, then Prometheus scrapes the API metrics. The API creates the response-log table on startup. Stop with `Ctrl+C`; use `docker compose down -v` only when you also want to delete the local database, cache, and Prometheus data volumes.

### MySQL response logging

The Compose setup creates a MySQL 8.4 database named `weather` and a database user named `weather`. The local evaluation password is `sezzle` for both the app user and MySQL root. These are weak, development-only credentials; replace them with distinct secrets before exposing the service beyond your local machine. On an already initialized `mysql_data` volume, changing these environment values does not rotate the MySQL accounts; update the existing accounts separately.

Start the stack with `docker compose up --build`. Compose passes the MySQL connection URL to the app and waits for MySQL to become healthy. The app creates the `weather_requests` table on startup and fails startup if schema initialization fails. It also verifies that the application user can insert a log row inside a transaction and rolls that probe back. Each successful weather lookup is recorded there, whether the response came from Open-Meteo or Redis. A database write problem is logged and counted but does not discard an otherwise successful weather response.

After checking a city in the browser, verify database readiness:

```sh
curl -i http://localhost:8000/ready
```

The response should report `"database":"ok"`. To inspect the saved rows, connect to the MySQL container and run a query:

```sh
docker compose exec mysql mysql -uweather -p weather
```

Enter the configured `MYSQL_PASSWORD` when prompted, then run:

```sql
SELECT id, requested_location, response_location, cache_hit, requested_at, response_json
FROM weather_requests
ORDER BY id DESC
LIMIT 10;
```

Type `exit` to leave the MySQL client. The `mysql_data` Compose volume preserves the database across container restarts. `docker compose down -v` deletes that volume and its logged responses.

## Run without Docker

Python 3.12+ is required. The Compose MySQL and Redis services can provide local dependencies while Uvicorn runs on the host; their ports are bound to loopback only.
note: I used docker here for the redis and mysql instances but if you dont want to use docker then install mysql and redis localy and modify the connection details in .env file 

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
docker compose up -d mysql redis
uvicorn app.main:app --reload
```

The sample `DATABASE_URL` points to `localhost:3306` using the local sample credentials, and `REDIS_URL` points to `localhost:6379`. If you use different credentials or ports, update these URLs in `.env`. Keep the Compose app stopped while running host Uvicorn so both processes do not compete for port 8000. Stop the dependency containers with `docker compose stop mysql redis` when finished.

## API

| Method and path | Behavior |
| --- | --- |
| `GET /` | Browser page with a city input that displays current weather. |
| `GET /weather/{city}` | Returns current metric weather. The city is trimmed and constrained to 1-120 characters. |
| `GET /health` | Liveness check for the API process. |
| `GET /ready` | Readiness checks for MySQL and Redis. Returns 503 with per-dependency status when degraded. |
| `GET /metrics` | Prometheus text exposition, scraped by the Compose Prometheus service; excluded from OpenAPI. |
| `GET /docs` | Interactive OpenAPI documentation. |

Example:

```sh
open http://localhost:8000/
curl -i http://localhost:8000/weather/London
curl -i http://localhost:8000/health
curl -i http://localhost:8000/ready
curl -s http://localhost:8000/metrics | grep weather_service_
```

Weather responses contain `location`, `temperature_c`, `feels_like_c`, `humidity_percent`, `description`, and UTC `observed_at`. Responses include an `X-Request-ID`; clients may supply one to correlate logs, capped at 128 characters. Do not put sensitive information in request IDs.

## Reliability and observability

- Cache-aside Redis entries use a configurable 300-second default TTL and a normalized city key. Cache outages are logged and counted, then the request falls through to Open-Meteo.
- On a cache miss, the service resolves the city with Open-Meteo geocoding and fetches current conditions by coordinates. The shared async HTTP client has explicit connect, read, and pool timeouts plus connection limits. Tenacity retries timeouts, network errors, and only upstream 500/502/503/504 responses, with at most three attempts per call and randomized backoff capped at one second. A 20-second total deadline bounds the complete lookup, and a semaphore caps concurrent upstream lookups at 20 per app process. Other 4xx/5xx responses and 429 are not retried. Not-found maps to 404, rate limiting to 503, provider failures to 502, and timeouts to 504.
- Successful upstream or cached results are recorded in `weather_requests`, including requested and resolved location, JSON response, cache-hit flag, and request time. A database write failure is logged and counted without discarding a valid weather result.
- JSON logs include timestamp, level, logger, and relevant request context. Prometheus metrics include request count/latency by route, provider outcomes/latency, cache outcomes, database operation outcomes/latency, and SQLAlchemy pool connection/check-out gauges. Request method labels are normalized to standard methods or `other`.
- Compose mounts `prometheus.yml/prometheus.yml` and `prometheus.yml/alert_rules.yml`; view active rule states at `http://localhost:9090/alerts`. Rules cover target availability, elevated API 5xx and provider failure rates, and MySQL response-log write errors. The sample stack evaluates alerts in Prometheus but does not configure an Alertmanager receiver; add one to deliver notifications.
- `/health` is a dependency-free liveness probe. `/ready` checks Redis, the `weather_requests` table, and response-log write health; it reports degraded (503) if any check fails. Startup verifies MySQL insert permission using a rolled-back probe row, and runtime response-log write failures mark logging unavailable until a later write succeeds. The weather route itself can still serve during a Redis or MySQL outage where possible.
- Infra and SRE security controls: TLS enforcement, bearer-token gateway auth, and request rate limiting are exposed via Prometheus counters (`weather_service_tls_requests_total`, `weather_service_auth_requests_total`, and `weather_service_rate_limit_total`) so operators can alert on unauthorized access, non-TLS traffic, and traffic bursts at the edge.
- All metrics use bounded labels; location, API key, and request ID are deliberately not metric labels.

The app creates the table at startup to keep a take-home demo easy to run. For a long-lived production deployment, replace this with versioned Alembic migrations, use managed secrets and TLS, add authentication/rate limiting, and configure an Alertmanager receiver. Compose requires DB passwords and keeps the unauthenticated API, Prometheus UI, and `/metrics` on loopback; production deployments should put them behind suitable network controls and protect metrics access. The concurrency cap is per app process, so size it with the number of workers and provider quota in mind.

## Tests

The tests mock Open-Meteo and use in-memory cache/repository doubles, so they need no API key, Docker, or live services:

```sh
python -m pip install -r requirements-dev.txt
pytest -q
```

## Configuration

See `.env.example`. Compose requires `MYSQL_PASSWORD` and `MYSQL_ROOT_PASSWORD`; non-Compose startup also requires a valid `DATABASE_URL`. Other important values are `REDIS_URL`, `CACHE_TTL_SECONDS`, `UPSTREAM_CONNECT_TIMEOUT_SECONDS`, `UPSTREAM_READ_TIMEOUT_SECONDS`, `UPSTREAM_POOL_TIMEOUT_SECONDS`, `UPSTREAM_RETRY_ATTEMPTS`, `UPSTREAM_TOTAL_TIMEOUT_SECONDS`, and `UPSTREAM_MAX_CONCURRENCY`. Open-Meteo does not require credentials. Never commit `.env` or real credentials.