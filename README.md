# open-meteo-etl-pipeline

Incremental ETL pipeline that pulls hourly weather forecasts from the [Open-Meteo API](https://open-meteo.com) into Databricks Unity Catalog, following a medallion (bronze / silver / gold) architecture.

A learning project: start with simple batch ingestion, move to incremental batch runs, and eventually streaming.

## How it works

1. **City dimension** (`madellion/bronze/ingest_cities`) — resolves a configured list of cities (name + country) through the Open-Meteo geocoding API and maintains `workspace.open_meteo.dim_city` with an idempotent `MERGE` keyed on the GeoNames `id`.
2. **Bronze ingest** (`madellion/bronze/ingest_forcast`) — reads the locations from `dim_city`, fetches the 7-day hourly forecast for all of them in batched async requests, and **appends** the flattened hourly rows to `workspace.open_meteo.bronze_hourly_forecast`, stamped with `fetched_at`. Because the 7-day window shifts each run, consecutive snapshots overlap; bronze keeps the full history of forecast revisions on purpose.
3. **Silver dedup** (`madellion/silver/dedup_forcast`) — rebuilds `workspace.open_meteo.silver_hourly_forecast` with only the **latest forecast** per `(latitude, longitude, forecast_time)`, picked with `row_number()` ordered by `fetched_at DESC`.

## Tables

| Table | Layer | Purpose |
| --- | --- | --- |
| `workspace.open_meteo.dim_city` | dimension | Geocoded city metadata (id, coordinates, timezone, elevation) |
| `workspace.open_meteo.bronze_hourly_forecast` | bronze | Raw hourly forecast snapshots, full history of revisions |
| `workspace.open_meteo.silver_hourly_forecast` | silver | Latest forecast per location and hour |

## Repository layout

```text
open-meteo-etl-pipeline/
├── open_meteo/            # Python package consumed by the notebooks
│   ├── client.py           # Async API client (httpx + pydantic)
│   ├── schemas.py          # Pydantic models for API responses
│   └── exceptions.py       # OpenMeteoError, GeocodingError, ForecastError
├── madellion/              # Medallion layer notebooks
│   ├── bronze/            # ingest_cities, ingest_forcast
│   └── silver/            # dedup_forcast
├── experiments/           # dev_tests, synchronous_requests
├── tests/                 # pytest suite (asyncio_mode = auto)
└── pyproject.toml
```

## The `open_meteo` package

A typed async client around the two Open-Meteo endpoints, used by the notebooks:

- `geocode_cities(cities)` — geocodes `City(name, country_code)` values concurrently; returns the top match per city (`None` when no match).
- `fetch_hourly_forecast(locations)` — fetches hourly forecasts for `Location(latitude, longitude)` values, batching up to 20 locations per request with bounded concurrency.

Design details:

- **Validation** — every response is parsed into a pydantic model; unit fields are `Literal` types, so validation fails if the API ever returns unexpected units.
- **Retries** — GETs retry on 429 / 5xx with exponential backoff and `Retry-After` support, at both the transport and request level.
- **Resilience** — geocoding/forecast failures for one city or batch are logged and skipped rather than failing the whole run; unexpected exceptions are re-raised.
- **Deduplication** — duplicate cities/locations are requested only once.
- **Coordinates** — each forecast carries the grid coordinates the API actually used, which can differ slightly from the requested ones; match on those, not on position.

## Setup

Requires Python 3.12+.

```bash
pip install -e .           # runtime: httpx, pydantic
pip install -e ".[dev]"    # dev tooling: black, ruff, mypy, pytest
```

The notebooks run on Databricks serverless compute and import the package from the repo root (add the repo folder to `sys.path` or install the wheel on the cluster).

## Development

```bash
pytest        # run the test suite (async tests run automatically)
ruff check .  # lint
black .       # format
mypy .        # type-check
```
