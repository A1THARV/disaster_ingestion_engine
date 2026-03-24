"""
DisasterIntel — Open-Meteo Weather Ingestion
Free API, no key required. Global coverage.
Combines multiple national weather models for best accuracy per location.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

from models.events import (
    DisasterEvent, DisasterType, GeoLocation, Severity,
    SourceType, WeatherSnapshot,
)
from config.settings import config

logger = logging.getLogger(__name__)


class OpenMeteoIngestor:
    """Fetches weather data from Open-Meteo for monitored locations."""

    def __init__(self):
        self.config = config.open_meteo
        self.scoring = config.scoring

    async def fetch_weather(
        self, session: aiohttp.ClientSession, location: Dict
    ) -> Optional[Dict]:
        """Fetch weather data for a single location."""
        params = {
            "latitude": location["lat"],
            "longitude": location["lon"],
            "hourly": self.config.hourly_params,
            "daily": self.config.daily_params,
            "timezone": self.config.timezone,
            "forecast_days": self.config.forecast_days,
            "current": (
                "temperature_2m,relative_humidity_2m,precipitation,rain,"
                "weather_code,wind_speed_10m,wind_gusts_10m,wind_direction_10m"
            ),
        }

        try:
            async with session.get(
                self.config.base_url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    data["_location_name"] = location["name"]
                    logger.info(f"[OpenMeteo] Fetched weather for {location['name']}")
                    return data
                else:
                    logger.error(
                        f"[OpenMeteo] HTTP {resp.status} for {location['name']}"
                    )
                    return None
        except Exception as e:
            logger.error(f"[OpenMeteo] Error fetching {location['name']}: {e}")
            return None

    async def fetch_all_locations(self) -> List[Dict]:
        """Fetch weather for all monitored locations concurrently."""
        async with aiohttp.ClientSession() as session:
            tasks = [
                self.fetch_weather(session, loc)
                for loc in config.watch_locations
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [r for r in results if r and not isinstance(r, Exception)]

    def parse_current_weather(self, raw: Dict) -> Optional[WeatherSnapshot]:
        """Parse current weather conditions into a WeatherSnapshot."""
        current = raw.get("current")
        if not current:
            return None

        loc_name = raw.get("_location_name", "Unknown")
        lat = raw.get("latitude", 0)
        lon = raw.get("longitude", 0)

        snapshot = WeatherSnapshot(
            location=GeoLocation(
                latitude=lat, longitude=lon, location_name=loc_name
            ),
            timestamp=datetime.fromisoformat(
                current.get("time", datetime.utcnow().isoformat())
            ),
            source="open_meteo",
            temperature_c=current.get("temperature_2m"),
            humidity_pct=current.get("relative_humidity_2m"),
            precipitation_mm=current.get("precipitation"),
            rain_mm=current.get("rain"),
            wind_speed_kmh=current.get("wind_speed_10m"),
            wind_gusts_kmh=current.get("wind_gusts_10m"),
            wind_direction_deg=current.get("wind_direction_10m"),
            weather_code=current.get("weather_code"),
        )
        return snapshot

    def parse_hourly_snapshots(
        self, raw: Dict, hours: int = 24
    ) -> List[WeatherSnapshot]:
        """Parse hourly forecast into WeatherSnapshots (default: next 24h)."""
        hourly = raw.get("hourly", {})
        times = hourly.get("time", [])[:hours]
        loc_name = raw.get("_location_name", "Unknown")
        lat = raw.get("latitude", 0)
        lon = raw.get("longitude", 0)

        snapshots = []
        for i, t in enumerate(times):
            snapshot = WeatherSnapshot(
                location=GeoLocation(
                    latitude=lat, longitude=lon, location_name=loc_name
                ),
                timestamp=datetime.fromisoformat(t),
                source="open_meteo",
                temperature_c=_safe_index(hourly.get("temperature_2m"), i),
                humidity_pct=_safe_index(hourly.get("relative_humidity_2m"), i),
                precipitation_mm=_safe_index(hourly.get("precipitation"), i),
                rain_mm=_safe_index(hourly.get("rain"), i),
                wind_speed_kmh=_safe_index(hourly.get("wind_speed_10m"), i),
                wind_gusts_kmh=_safe_index(hourly.get("wind_gusts_10m"), i),
                wind_direction_deg=_safe_index(hourly.get("wind_direction_10m"), i),
                weather_code=_safe_index(hourly.get("weather_code"), i),
                cape_jkg=_safe_index(hourly.get("cape"), i),
                soil_moisture=_safe_index(hourly.get("soil_moisture_0_to_1cm"), i),
                visibility_m=_safe_index(hourly.get("visibility"), i),
                cloud_cover_pct=_safe_index(hourly.get("cloud_cover"), i),
                snow_depth_m=_safe_index(hourly.get("snow_depth"), i),
            )
            snapshots.append(snapshot)
        return snapshots

    def detect_severe_events(
        self, snapshots: List[WeatherSnapshot]
    ) -> List[DisasterEvent]:
        """
        USP: Convert severe weather snapshots into DisasterEvents.
        This is where weather data becomes actionable alerts.
        """
        events = []
        for snap in snapshots:
            if not snap.is_severe_weather():
                continue

            # Determine disaster type from weather conditions
            disaster_type = self._classify_weather_disaster(snap)
            if disaster_type == DisasterType.UNKNOWN:
                continue
            severity = snap.get_severity()

            event = DisasterEvent(
                event_id=DisasterEvent.generate_id(
                    "open_meteo",
                    f"{snap.location.location_name}_{snap.timestamp.isoformat()}"
                ),
                source="open_meteo",
                source_type=SourceType.WEATHER_API,
                disaster_type=disaster_type,
                severity=severity,
                location=snap.location,
                timestamp=snap.timestamp,
                title=self._build_weather_title(snap, disaster_type),
                description=self._build_weather_description(snap),
                precipitation_mm=snap.precipitation_mm,
                wind_speed_kmh=snap.wind_speed_kmh,
                raw_data=snap.to_dict(),
                # Initial confidence is moderate (needs cross-verification)
                confidence_score=55.0,
            )
            event.compute_freshness(config.scoring.freshness_half_life_minutes)
            events.append(event)

        return events

    def _classify_weather_disaster(self, snap: WeatherSnapshot) -> DisasterType:
        """Classify what type of disaster the weather indicates."""
        precip = snap.precipitation_mm or 0
        wind = snap.wind_speed_kmh or 0
        gusts = snap.wind_gusts_kmh or 0
        wcode = snap.weather_code or 0
        cape = snap.cape_jkg or 0

        if precip > 30 and (snap.soil_moisture or 0) > 0.4:
            return DisasterType.FLOOD
        if wind > 120 or gusts > 140:
            return DisasterType.CYCLONE
        if wcode >= 95 and (wind > 40 or gusts > 60 or precip > 5 or cape > 2000):
            return DisasterType.STORM
        if precip > 30:
            return DisasterType.FLOOD
        if wind > 90:
            return DisasterType.STORM
        if cape > 2200 and (precip > 10 or wind > 45 or wcode >= 80):
            return DisasterType.STORM
        return DisasterType.UNKNOWN

    def _build_weather_title(
        self, snap: WeatherSnapshot, dtype: DisasterType
    ) -> str:
        loc = snap.location.location_name or "Unknown"
        return f"{dtype.value.title()} conditions detected in {loc}"

    def _build_weather_description(self, snap: WeatherSnapshot) -> str:
        parts = []
        if snap.precipitation_mm:
            parts.append(f"Precipitation: {snap.precipitation_mm:.1f} mm/hr")
        if snap.wind_speed_kmh:
            parts.append(f"Wind: {snap.wind_speed_kmh:.1f} km/h")
        if snap.wind_gusts_kmh:
            parts.append(f"Gusts: {snap.wind_gusts_kmh:.1f} km/h")
        if snap.cape_jkg:
            parts.append(f"CAPE: {snap.cape_jkg:.0f} J/kg")
        if snap.weather_code:
            parts.append(f"WMO Code: {snap.weather_code}")
        if snap.cape_jkg and not parts:
            parts.append(f"CAPE: {snap.cape_jkg:.0f} J/kg")
        return " | ".join(parts) if parts else "Weather anomaly detected"

    async def ingest(self) -> Tuple[List[WeatherSnapshot], List[DisasterEvent]]:
        """
        Main ingestion entry point.
        Returns (all_snapshots, severe_events).
        """
        logger.info("[OpenMeteo] Starting ingestion for all locations...")
        raw_responses = await self.fetch_all_locations()

        all_snapshots = []
        all_events = []

        for raw in raw_responses:
            # Parse current + hourly
            current = self.parse_current_weather(raw)
            if current:
                all_snapshots.append(current)

            hourly = self.parse_hourly_snapshots(raw)
            all_snapshots.extend(hourly)

            # Detect severe events from hourly data
            events = self.detect_severe_events(hourly)
            all_events.extend(events)

            # Also check current conditions
            if current:
                current_events = self.detect_severe_events([current])
                all_events.extend(current_events)

        logger.info(
            f"[OpenMeteo] Ingested {len(all_snapshots)} snapshots, "
            f"detected {len(all_events)} severe events"
        )
        return all_snapshots, all_events


def _safe_index(lst: Optional[list], idx: int):
    """Safely index into a list, returning None if out of bounds."""
    if lst is None or idx >= len(lst):
        return None
    return lst[idx]
