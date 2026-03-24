"""
DisasterIntel — India Meteorological Department (IMD) Ingestion
Free API with IP whitelisting. India-specific authoritative data.
Provides city forecasts, current weather, district rainfall, and nowcasts.

Note: IMD API may require IP whitelisting for production use.
For hackathon/MVP, we attempt direct access and fall back gracefully.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiohttp

from models.events import (
    DisasterEvent, DisasterType, GeoLocation, Severity,
    SourceType, WeatherSnapshot,
)
from config.settings import config

logger = logging.getLogger(__name__)


class IMDIngestor:
    """Fetches weather data from India Meteorological Department."""

    def __init__(self):
        self.config = config.imd

    async def fetch_city_forecast(
        self, session: aiohttp.ClientSession, location: Dict
    ) -> Optional[Dict]:
        """Fetch 7-day city forecast from IMD."""
        imd_id = location.get("imd_id")
        if not imd_id:
            return None

        url = f"{self.config.city_forecast_url}?id={imd_id}"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # IMD returns JSON but sometimes with quirks
                    import json
                    try:
                        data = json.loads(text)
                        data["_location_name"] = location["name"]
                        data["_lat"] = location["lat"]
                        data["_lon"] = location["lon"]
                        logger.info(f"[IMD] Fetched forecast for {location['name']}")
                        return data
                    except json.JSONDecodeError:
                        logger.warning(f"[IMD] Invalid JSON for {location['name']}")
                        return None
                else:
                    logger.warning(f"[IMD] HTTP {resp.status} for {location['name']}")
                    return None
        except Exception as e:
            logger.error(f"[IMD] Error fetching {location['name']}: {e}")
            return None

    async def fetch_current_weather(
        self, session: aiohttp.ClientSession, location: Dict
    ) -> Optional[Dict]:
        """Fetch current weather observation from IMD."""
        imd_id = location.get("imd_id")
        if not imd_id:
            return None

        url = f"{self.config.current_weather_url}?id={imd_id}"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    import json
                    try:
                        data = json.loads(text)
                        data["_location_name"] = location["name"]
                        data["_lat"] = location["lat"]
                        data["_lon"] = location["lon"]
                        logger.info(f"[IMD] Fetched current weather for {location['name']}")
                        return data
                    except json.JSONDecodeError:
                        logger.warning(f"[IMD] Invalid JSON for current weather {location['name']}")
                        return None
                else:
                    logger.warning(f"[IMD] HTTP {resp.status} for current {location['name']}")
                    return None
        except Exception as e:
            logger.error(f"[IMD] Error fetching current {location['name']}: {e}")
            return None

    async def fetch_district_rainfall(
        self, session: aiohttp.ClientSession, district_id: str
    ) -> Optional[Dict]:
        """Fetch district-wise rainfall data."""
        url = f"{self.config.district_rainfall_url}?id={district_id}"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    import json
                    text = await resp.text()
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return None
                return None
        except Exception as e:
            logger.error(f"[IMD] Error fetching rainfall for district {district_id}: {e}")
            return None

    def parse_current_weather(self, raw: Dict) -> Optional[WeatherSnapshot]:
        """Parse IMD current weather into WeatherSnapshot."""
        if not raw:
            return None

        loc_name = raw.get("_location_name", raw.get("Station", "Unknown"))
        lat = raw.get("_lat", raw.get("Latitude", 0))
        lon = raw.get("_lon", raw.get("Longitude", 0))

        # Parse temperature — IMD provides max/min separately
        try:
            max_temp = float(raw.get("MAX_TEMP", 0) or 0)
            min_temp = float(raw.get("MIN_TEMP", 0) or 0)
            temp = (max_temp + min_temp) / 2 if max_temp and min_temp else max_temp or min_temp
        except (ValueError, TypeError):
            temp = None

        # Parse wind speed
        try:
            wind_speed = float(raw.get("WIND_SPEED", 0) or 0)
            # IMD reports in knots, convert to km/h
            wind_speed_kmh = wind_speed * 1.852 if wind_speed else None
        except (ValueError, TypeError):
            wind_speed_kmh = None

        # Parse humidity
        try:
            humidity = float(raw.get("Humidity", 0) or 0)
        except (ValueError, TypeError):
            humidity = None

        # Parse observation time
        try:
            date_str = raw.get("Date of Observation", "")
            timestamp = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.utcnow()
        except (ValueError, TypeError):
            timestamp = datetime.utcnow()

        snapshot = WeatherSnapshot(
            location=GeoLocation(
                latitude=float(lat) if lat else 0,
                longitude=float(lon) if lon else 0,
                location_name=loc_name,
                country="India",
            ),
            timestamp=timestamp,
            source="imd",
            temperature_c=temp,
            humidity_pct=humidity,
            wind_speed_kmh=wind_speed_kmh,
            weather_code=_parse_int(raw.get("WEATHER_CODE")),
            cloud_cover_pct=_parse_float(raw.get("NEBULOSITY")),
        )
        return snapshot

    def parse_forecast(self, raw: Dict) -> List[Dict]:
        """Parse IMD 7-day forecast into a list of daily summaries."""
        if not raw:
            return []

        loc_name = raw.get("_location_name", "Unknown")
        forecasts = []

        for day_num in range(1, 8):
            prefix = f"Day_{day_num}" if day_num > 1 else "Todays"
            max_key = f"{prefix}_Max_Temp" if day_num > 1 else "Todays_Forecast_Max_temp"
            min_key = f"{prefix}_Min_temp" if day_num > 1 else "Todays_Forecast_Min_temp"
            forecast_key = f"{prefix}_Forecast" if day_num > 1 else "Todays_Forecast"

            forecast_text = raw.get(forecast_key, "")
            if forecast_text:
                forecasts.append({
                    "day": day_num,
                    "location": loc_name,
                    "max_temp": _parse_float(raw.get(max_key)),
                    "min_temp": _parse_float(raw.get(min_key)),
                    "forecast": forecast_text,
                    "source": "imd",
                })
        return forecasts

    def detect_severe_from_forecast(self, forecasts: List[Dict]) -> List[DisasterEvent]:
        """Detect severe weather from IMD forecast text using keyword matching."""
        severe_keywords = {
            DisasterType.FLOOD: [
                "heavy rain", "very heavy rain", "extremely heavy rain",
                "flood", "waterlogging", "deluge",
            ],
            DisasterType.CYCLONE: [
                "cyclone", "cyclonic", "hurricane", "typhoon",
                "deep depression", "severe cyclonic",
            ],
            DisasterType.STORM: [
                "thunderstorm", "lightning", "hailstorm", "squall",
                "dust storm", "storm",
            ],
            DisasterType.HEATWAVE: [
                "heat wave", "heatwave", "severe heat",
            ],
        }

        events = []
        for fc in forecasts:
            text = (fc.get("forecast") or "").lower()
            if not text:
                continue

            for dtype, keywords in severe_keywords.items():
                if any(kw in text for kw in keywords):
                    severity = Severity.HIGH if any(
                        w in text for w in ["very heavy", "extremely", "severe", "cyclonic"]
                    ) else Severity.MODERATE

                    event = DisasterEvent(
                        event_id=DisasterEvent.generate_id(
                            "imd", f"{fc['location']}_day{fc['day']}_{datetime.utcnow().date()}"
                        ),
                        source="imd",
                        source_type=SourceType.GOVERNMENT_ALERT,
                        disaster_type=dtype,
                        severity=severity,
                        location=GeoLocation(
                            latitude=0, longitude=0,
                            location_name=fc["location"],
                            country="India",
                        ),
                        timestamp=datetime.utcnow(),
                        title=f"[IMD] {dtype.value.title()} forecast for {fc['location']} (Day {fc['day']})",
                        description=fc["forecast"],
                        # IMD is a government source — high initial confidence
                        confidence_score=80.0,
                        raw_data=fc,
                    )
                    event.compute_freshness(config.scoring.freshness_half_life_minutes)
                    events.append(event)
                    break  # One event per forecast entry

        return events

    async def ingest(self) -> Tuple[List[WeatherSnapshot], List[DisasterEvent]]:
        """
        Main ingestion entry point.
        Returns (weather_snapshots, severe_events).
        """
        logger.info("[IMD] Starting ingestion for Indian locations...")
        all_snapshots = []
        all_events = []

        async with aiohttp.ClientSession() as session:
            # Fetch current weather + forecasts concurrently for all locations
            current_tasks = []
            forecast_tasks = []
            for loc in config.watch_locations:
                current_tasks.append(self.fetch_current_weather(session, loc))
                forecast_tasks.append(self.fetch_city_forecast(session, loc))

            current_results = await asyncio.gather(*current_tasks, return_exceptions=True)
            forecast_results = await asyncio.gather(*forecast_tasks, return_exceptions=True)

            # Process current weather
            for raw in current_results:
                if raw and not isinstance(raw, Exception):
                    snap = self.parse_current_weather(raw)
                    if snap:
                        all_snapshots.append(snap)

            # Process forecasts
            for raw in forecast_results:
                if raw and not isinstance(raw, Exception):
                    forecasts = self.parse_forecast(raw)
                    events = self.detect_severe_from_forecast(forecasts)
                    all_events.extend(events)

        logger.info(
            f"[IMD] Ingested {len(all_snapshots)} snapshots, "
            f"detected {len(all_events)} severe forecast events"
        )
        return all_snapshots, all_events


def _parse_float(val) -> Optional[float]:
    try:
        return float(val) if val else None
    except (ValueError, TypeError):
        return None


def _parse_int(val) -> Optional[int]:
    try:
        return int(float(val)) if val else None
    except (ValueError, TypeError):
        return None
