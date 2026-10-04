from datetime import datetime, timezone

from pydantic import BaseModel, Field


class WeatherResponse(BaseModel):
    location: str
    temperature_c: float
    feels_like_c: float
    humidity_percent: int = Field(ge=0, le=100)
    description: str
    observed_at: datetime

    @classmethod
    def from_openmeteo(cls, location: str, payload: dict) -> "WeatherResponse":
        current = payload["current"]
        observed_at = datetime.fromisoformat(current["time"].replace("Z", "+00:00"))
        weather_descriptions = {
            0: "clear sky",
            1: "mainly clear",
            2: "partly cloudy",
            3: "overcast",
            45: "fog",
            48: "depositing rime fog",
            51: "light drizzle",
            53: "moderate drizzle",
            55: "dense drizzle",
            56: "light freezing drizzle",
            57: "dense freezing drizzle",
            61: "slight rain",
            63: "moderate rain",
            65: "heavy rain",
            66: "light freezing rain",
            67: "heavy freezing rain",
            71: "slight snow",
            73: "moderate snow",
            75: "heavy snow",
            77: "snow grains",
            80: "slight rain showers",
            81: "moderate rain showers",
            82: "violent rain showers",
            85: "slight snow showers",
            86: "heavy snow showers",
            95: "thunderstorm",
            96: "thunderstorm with slight hail",
            99: "thunderstorm with heavy hail",
        }
        return cls(
            location=location,
            temperature_c=current["temperature_2m"],
            feels_like_c=current["apparent_temperature"],
            humidity_percent=current["relative_humidity_2m"],
            description=weather_descriptions.get(current["weather_code"], "unknown conditions"),
            observed_at=observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at,
        )


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str]