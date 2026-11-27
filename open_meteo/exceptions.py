class OpenMeteoError(Exception):
    """Base exception for Open-Meteo API client failures."""


class GeocodingError(OpenMeteoError):
    """Raised when a geocoding request or response validation fails."""


class ForecastError(OpenMeteoError):
    """Raised when a forecast request or response validation fails."""