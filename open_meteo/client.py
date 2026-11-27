"""Geocode cities and fetch their hourly forecasts from the Open-Meteo APIs."""

import asyncio
import logging
import random
from collections.abc import Sequence
from itertools import batched
from typing import Any, NamedTuple

from httpx import AsyncClient, AsyncHTTPTransport, HTTPError, HTTPStatusError, Response
from pydantic import TypeAdapter, ValidationError

from open_meteo.exceptions import ForecastError, OpenMeteoError
from open_meteo.schemas import GeocodingResponse, GeocodingResult, HourlyForecastResponse

log = logging.getLogger("open_meteo.dim_city")


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_CONCURRENCY = 10
BATCH_SIZE = 20
TIMEOUT = 30
RETRIES = 3
MAX_RETRIES = 3
MAX_RETRY_WAIT = 60
HOURLY_VARIABLES = ",".join(
    (
        "temperature_2m",
        "precipitation",
        "weather_code",
        "wind_speed_10m",
        "apparent_temperature",
    )
)
HOURLY_FORECASTS = TypeAdapter(list[HourlyForecastResponse])
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class City(NamedTuple):
    """A city to geocode.

    Attributes
    ----------
    name : str
        City name, e.g. ``"Kolkata"``.
    country_code : str
        ISO-3166-1 alpha-2 country code, e.g. ``"IN"``.
    """

    name: str
    country_code: str


class Location(NamedTuple):
    """A point to request a forecast for.

    Attributes
    ----------
    latitude : float
        WGS84 latitude in decimal degrees.
    longitude : float
        WGS84 longitude in decimal degrees.
    """

    latitude: float
    longitude: float


def _format_error(base: str, exc: HTTPError | ValidationError) -> str:
    if isinstance(exc, HTTPStatusError):
        return f"{base} (HTTP {exc.response.status_code})"
    if isinstance(exc, HTTPError):
        return f"{base} ({type(exc).__name__}: {exc})"
    return f"{base} (validation error: {exc})"


def _retry_delay(response: Response, attempt: int) -> float:
    """Seconds to wait before the next attempt: ``Retry-After`` or backoff."""
    retry_after = response.headers.get("retry-after")
    if retry_after is not None and retry_after.isdigit():
        return float(retry_after)
    return min(2.0**attempt, MAX_RETRY_WAIT) + random.uniform(0, 1)


async def get_with_retry(
    client: AsyncClient,
    url: str,
    params: dict[str, Any],
) -> Response:
    """GET, retrying on 429 and 5xx with backoff and ``Retry-After``.

    Parameters
    ----------
    client : AsyncClient
        The HTTP client used for the requests.
    url : str
        The URL to request.
    params : dict[str, Any]
        Query parameters.

    Returns
    -------
    response : Response
        The first response that is not retryable, or the last one once the
        retries run out or the required wait exceeds ``MAX_RETRY_WAIT``. The
        caller still has to call ``raise_for_status``.
    """
    for attempt in range(MAX_RETRIES):
        response = await client.get(url, params=params)
        if response.status_code not in RETRY_STATUS:
            return response
        delay = _retry_delay(response, attempt)
        if delay > MAX_RETRY_WAIT:
            return response
        await asyncio.sleep(delay)
    return await client.get(url, params=params)


async def fetch_city_location(
    city: City,
    client: AsyncClient,
    sem: asyncio.Semaphore,
) -> GeocodingResult | None:
    """Fetch the best geocoding match for a single city.

    Parameters
    ----------
    city : City
        City to look up.
    client : AsyncClient
        Shared HTTP client used to make the request.
    sem : asyncio.Semaphore
        Semaphore bounding the number of concurrent requests.

    Returns
    -------
    match : GeocodingResult | None
        Top match, or ``None`` if the API returned no results.

    Raises
    ------
    OpenMeteoError
        If the request fails (network error or non-2xx status) or the
        response cannot be parsed or validated.
    """
    async with sem:
        try:
            response = await get_with_retry(
                client,
                GEOCODING_URL,
                params={
                    "name": city.name,
                    "countryCode": city.country_code,
                    "count": 1,
                    "language": "en",
                },
            )
            response.raise_for_status()
            data = GeocodingResponse.model_validate_json(response.content)
        except (HTTPError, ValidationError) as exc:
            raise OpenMeteoError(
                _format_error(f"Geocoding failed for {city}", exc)
            ) from exc
    return data.results[0] if data.results else None


async def geocode_cities(
    cities: list[City],
    *,
    max_concurrency: int = MAX_CONCURRENCY,
) -> dict[City, GeocodingResult | None]:
    """Geocode many cities concurrently.

    Duplicate cities are looked up once. Concurrency is capped at
    ``max_concurrency``.

    Parameters
    ----------
    cities : list[City]
        Cities to geocode.
    max_concurrency : int, default=MAX_CONCURRENCY
        Maximum number of lookups in flight at once.

    Returns
    -------
    matches : dict[City, GeocodingResult | None]
        Top match for each unique input city. A value of ``None`` means the
        API found no match, or the lookup failed with ``OpenMeteoError``
        (logged as a warning).

    Raises
    ------
    Exception
        Any exception other than ``OpenMeteoError`` raised during a lookup is
        re-raised unchanged so unexpected errors are not masked.
    """
    assert max_concurrency > 0, "max_concurrency must be positive"
    unique = list(dict.fromkeys(cities))
    sem = asyncio.Semaphore(max_concurrency)
    transport = AsyncHTTPTransport(retries=RETRIES)
    async with AsyncClient(transport=transport, timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            *(fetch_city_location(c, client, sem) for c in unique),
            return_exceptions=True,
        )

    out: dict[City, GeocodingResult | None] = {}
    for city, res in zip(unique, results, strict=True):
        if isinstance(res, OpenMeteoError):
            log.warning("Geocoding failed for %s", city, exc_info=res)
            out[city] = None
        elif isinstance(res, BaseException):
            raise res
        else:
            out[city] = res
    return out


async def fetch_hourly_forecast_batch(
    locations: Sequence[Location],
    client: AsyncClient,
    sem: asyncio.Semaphore,
) -> list[HourlyForecastResponse]:
    """Fetch hourly forecasts for several locations in a single request.

    Parameters
    ----------
    locations : Sequence[Location]
        The locations to fetch.
    client : AsyncClient
        The HTTP client used for the request.
    sem : asyncio.Semaphore
        Limits how many requests are in flight at once.

    Returns
    -------
    forecasts : list[HourlyForecastResponse]
        One validated forecast per location. Each forecast carries the grid
        coordinates the API used, which can differ slightly from the requested
        ones. Match forecasts to locations by those coordinates, not by position.

    Raises
    ------
    ForecastError
        If the request fails or the response does not match the model.
    """
    if not locations:
        return []
    async with sem:
        try:
            response = await get_with_retry(
                client,
                FORECAST_URL,
                params={
                    "latitude": ",".join(str(loc.latitude) for loc in locations),
                    "longitude": ",".join(str(loc.longitude) for loc in locations),
                    "hourly": HOURLY_VARIABLES,
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "ms",
                    "precipitation_unit": "mm",
                    "timeformat": "iso8601",
                    "timezone": "UTC",
                },
            )
            response.raise_for_status()
            if len(locations) == 1:
                data = [HourlyForecastResponse.model_validate_json(response.content)]
            else:
                data = HOURLY_FORECASTS.validate_json(response.content)
        except (HTTPError, ValidationError) as exc:
            raise ForecastError(
                _format_error(f"Forecast failed for {len(locations)} locations", exc)
            ) from exc
    return data


async def fetch_hourly_forecast(
    locations: Sequence[Location],
    *,
    batch_size: int = BATCH_SIZE,
    max_concurrency: int = MAX_CONCURRENCY,
) -> list[HourlyForecastResponse]:
    """Fetch hourly forecasts for all locations, in batches.

    Duplicate locations are requested once. The locations are split into
    batches of ``batch_size`` and the batches are requested concurrently. A
    batch that fails with a ``ForecastError`` is logged and skipped, so the
    result can be shorter than ``locations``.

    Parameters
    ----------
    locations : Sequence[Location]
        The locations to fetch.
    batch_size : int, default=BATCH_SIZE
        Number of locations sent in each request.
    max_concurrency : int, default=MAX_CONCURRENCY
        Maximum number of requests in flight at once.

    Returns
    -------
    forecasts : list[HourlyForecastResponse]
        The forecasts of all successful batches, flattened. Each forecast
        carries the grid coordinates the API used, which can differ slightly
        from the requested ones. Match forecasts to locations by those
        coordinates, not by position.

    Raises
    ------
    BaseException
        Any exception other than ``ForecastError`` raised by a batch is
        re-raised.
    """
    assert max_concurrency >= 1, "max_concurrency must be at least 1"
    assert batch_size >= 1, "batch_size must be at least 1"
    locations = list(dict.fromkeys(locations))
    sem = asyncio.Semaphore(max_concurrency)
    transport = AsyncHTTPTransport(retries=RETRIES)
    batches = [list(b) for b in batched(locations, batch_size)]
    async with AsyncClient(transport=transport, timeout=TIMEOUT) as client:
        outcomes = await asyncio.gather(
            *(fetch_hourly_forecast_batch(batch, client, sem) for batch in batches),
            return_exceptions=True,
        )

    results: list[HourlyForecastResponse] = []
    for batch, outcome in zip(batches, outcomes, strict=True):
        if isinstance(outcome, ForecastError):
            log.warning(
                "Forecast failed for a batch of %d locations (first: %s): %s",
                len(batch),
                batch[0],
                outcome,
            )
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            results.extend(outcome)
    return results