from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class GeocodingResult(BaseModel):
    """Geographic metadata for a single location returned by the geocoding API.

    Attributes
    ----------
    id : int
        Unique GeoNames identifier for the location.
    name : str
        Localized display name of the location.
    latitude : float
        WGS84 latitude in decimal degrees.
    longitude : float
        WGS84 longitude in decimal degrees.
    country_code : str
        ISO 3166-1 alpha-2 two-letter country code (e.g., ``"GB"``).
    timezone : str
        IANA time zone database identifier (e.g., ``"Europe/London"``).
    elevation : float
        Elevation above sea level in meters.
    country_id : int
        Unique GeoNames identifier for the parent country.
    country : str
        Full country name in the requested language.
    population : int or None, default=None
        Estimated population count, if recorded.
    feature_code : str or None, default=None
        GeoNames feature classification code (e.g., ``"PPLC"`` for capital).
    admin1 : str or None, default=None
        First-order administrative division name (e.g., state or region).
    admin1_id : int or None, default=None
        GeoNames identifier for the first-order administrative division.
    admin2 : str or None, default=None
        Second-order administrative division name (e.g., county or district).
    admin2_id : int or None, default=None
        GeoNames identifier for the second-order administrative division.
    """

    id: int
    name: str
    latitude: float
    longitude: float
    country_code: str
    timezone: str
    elevation: float
    country_id: int
    country: str
    population: int | None = None
    feature_code: str | None = None
    admin1: str | None = None
    admin1_id: int | None = None
    admin2: str | None = None
    admin2_id: int | None = None


class GeocodingResponse(BaseModel):
    """Top-level response payload from the Open-Meteo geocoding endpoint.

    Attributes
    ----------
    results : list of GeocodingResult, default=[]
        Ordered list of matching location records. Defaults to an empty list
        when the API omits ``results`` for queries with no matches.
    generationtime_ms : float
        Server-side query execution time in milliseconds.
    """

    results: list[GeocodingResult] = []
    generationtime_ms: float


class HourlyUnits(BaseModel):
    """Units reported by the API for each hourly variable.

    Each field is a ``Literal`` of the unit string the request asks for, so
    validation fails if the API returns a different unit than expected.

    Attributes
    ----------
    time : Literal["iso8601"]
        Format of the timestamps.
    temperature_2m : Literal["°C"]
        Unit of the air temperature at 2 m.
    precipitation : Literal["mm"]
        Unit of the hourly precipitation.
    weather_code : Literal["wmo code"]
        Code table of the weather codes. This is a WMO code table, not a
        measurement unit.
    wind_speed_10m : Literal["m/s"]
        Unit of the wind speed at 10 m.
    apparent_temperature : Literal["°C"]
        Unit of the apparent (perceived) temperature.
    """

    time: Literal["iso8601"]
    temperature_2m: Literal["°C"]
    precipitation: Literal["mm"]
    weather_code: Literal["wmo code"]
    wind_speed_10m: Literal["m/s"]
    apparent_temperature: Literal["°C"]


class HourlyForecast(BaseModel):
    """Hourly weather series for one location.

    Every list has one entry per hour and all lists have the same length.
    Entries are ``None`` where the API has no data for that hour.

    Attributes
    ----------
    time : list[datetime]
        Timestamps of each hour. Naive datetimes, in UTC when the request
        uses ``timezone=UTC``.
    temperature_2m : list[float or None]
        Air temperature 2 m above ground, in the requested temperature unit.
    precipitation : list[float or None]
        Total precipitation (rain, showers and snow) over the preceding hour,
        in the requested precipitation unit.
    weather_code : list[int or None]
        WMO weather interpretation code for each hour.
    wind_speed_10m : list[float or None]
        Wind speed 10 m above ground, in the requested wind speed unit.
    apparent_temperature : list[float or None]
        Perceived temperature including wind chill and humidity, in the
        requested temperature unit.
    """

    time: list[datetime]
    temperature_2m: list[float | None]
    precipitation: list[float | None]
    weather_code: list[int | None]
    wind_speed_10m: list[float | None]
    apparent_temperature: list[float | None]


class HourlyForecastResponse(BaseModel):
    """Forecast response for one location.

    Attributes
    ----------
    latitude : float
        Latitude of the grid cell used, in decimal degrees. It can differ
        slightly from the requested latitude.
    longitude : float
        Longitude of the grid cell used, in decimal degrees. It can differ
        slightly from the requested longitude.
    timezone : str
        IANA time zone identifier of the returned timestamps.
    hourly_units : HourlyUnits
        Units reported by the API for each hourly variable.
    hourly : HourlyForecast
        The hourly weather series.
    """

    latitude: float
    longitude: float
    timezone: str
    hourly_units: HourlyUnits
    hourly: HourlyForecast