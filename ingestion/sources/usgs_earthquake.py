"""
DisasterIntel — USGS Earthquake Ingestion
Free GeoJSON feeds, updated every minute.
Provides real-time earthquake data globally.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

from models.events import (
    DisasterEvent, DisasterType, GeoLocation, Severity, SourceType,
)
from config.settings import config

logger = logging.getLogger(__name__)


class USGSIngestor:
    """Fetches real-time earthquake data from USGS GeoJSON feeds."""

    def __init__(self):
        self.config = config.usgs

    async def fetch_feed(
        self, session: aiohttp.ClientSession, feed_url: str, label: str
    ) -> Optional[Dict]:
        """Fetch a USGS GeoJSON feed."""
        try:
            async with session.get(
                feed_url, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    count = len(data.get("features", []))
                    logger.info(f"[USGS] Fetched {label}: {count} earthquakes")
                    return data
                else:
                    logger.error(f"[USGS] HTTP {resp.status} for {label}")
                    return None
        except Exception as e:
            logger.error(f"[USGS] Error fetching {label}: {e}")
            return None

    async def fetch_all_feeds(self) -> List[Dict]:
        """Fetch multiple USGS feeds concurrently."""
        async with aiohttp.ClientSession() as session:
            feeds = [
                (self.config.significant_day, "significant_day"),
                (self.config.m45_day, "m4.5+_day"),
            ]
            tasks = [
                self.fetch_feed(session, url, label) for url, label in feeds
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [r for r in results if r and not isinstance(r, Exception)]

    async def fetch_by_region(
        self,
        session: aiohttp.ClientSession,
        min_lat: float, max_lat: float,
        min_lon: float, max_lon: float,
        min_magnitude: float = 3.0,
        days_back: int = 7,
    ) -> Optional[Dict]:
        """Query USGS for earthquakes in a specific bounding box."""
        from datetime import timedelta
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)

        params = {
            "format": "geojson",
            "starttime": start.strftime("%Y-%m-%d"),
            "endtime": end.strftime("%Y-%m-%d"),
            "minlatitude": min_lat,
            "maxlatitude": max_lat,
            "minlongitude": min_lon,
            "maxlongitude": max_lon,
            "minmagnitude": min_magnitude,
            "orderby": "time",
        }

        try:
            async with session.get(
                self.config.query_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(
                        f"[USGS] Regional query: {len(data.get('features', []))} quakes"
                    )
                    return data
                else:
                    logger.error(f"[USGS] HTTP {resp.status} for regional query")
                    return None
        except Exception as e:
            logger.error(f"[USGS] Error in regional query: {e}")
            return None

    def parse_earthquakes(self, geojson: Dict) -> List[DisasterEvent]:
        """Parse USGS GeoJSON features into DisasterEvents."""
        events = []
        features = geojson.get("features", [])

        for feature in features:
            props = feature.get("properties", {})
            geom = feature.get("geometry", {})
            coords = geom.get("coordinates", [0, 0, 0])

            # Extract core data
            magnitude = props.get("mag")
            if magnitude is None:
                continue

            # Coordinates: [longitude, latitude, depth]
            lon, lat = coords[0], coords[1]
            depth_km = coords[2] if len(coords) > 2 else None

            # Timestamp (USGS provides epoch milliseconds)
            time_ms = props.get("time", 0)
            timestamp = datetime.fromtimestamp(
                time_ms / 1000, tz=timezone.utc
            )

            # Determine severity
            severity = self._magnitude_to_severity(magnitude)

            # Build location info
            place = props.get("place", "Unknown location")
            location = GeoLocation(
                latitude=lat,
                longitude=lon,
                location_name=place,
            )

            # Estimate impact radius from magnitude
            impact_radius = self._estimate_impact_radius(magnitude, depth_km)

            # Check for tsunami alert
            tsunami = props.get("tsunami", 0)
            disaster_type = DisasterType.TSUNAMI if tsunami == 1 else DisasterType.EARTHQUAKE

            event = DisasterEvent(
                event_id=DisasterEvent.generate_id(
                    "usgs", props.get("code", props.get("ids", str(time_ms)))
                ),
                source="usgs",
                source_type=SourceType.SEISMIC_SENSOR,
                disaster_type=disaster_type,
                severity=severity,
                location=location,
                timestamp=timestamp,
                title=f"M{magnitude:.1f} {disaster_type.value.title()} — {place}",
                description=(
                    f"Magnitude {magnitude:.1f} earthquake at {depth_km:.1f} km depth. "
                    f"{'TSUNAMI ALERT ISSUED. ' if tsunami else ''}"
                    f"Felt report: {props.get('felt', 'N/A')} responses. "
                    f"Alert level: {props.get('alert', 'N/A')}."
                ),
                magnitude=magnitude,
                depth_km=depth_km,
                impact_radius_km=impact_radius,
                alert_level=props.get("alert"),  # green/yellow/orange/red
                source_url=props.get("url"),
                # USGS is authoritative seismic data — very high confidence
                confidence_score=95.0,
                raw_data={
                    "mag": magnitude,
                    "place": place,
                    "time": time_ms,
                    "depth": depth_km,
                    "tsunami": tsunami,
                    "felt": props.get("felt"),
                    "cdi": props.get("cdi"),  # Community Decimal Intensity
                    "mmi": props.get("mmi"),  # Modified Mercalli Intensity
                    "alert": props.get("alert"),
                    "sig": props.get("sig"),  # Significance 0-1000
                    "type": props.get("type"),
                    "url": props.get("url"),
                },
            )
            event.compute_freshness(config.scoring.freshness_half_life_minutes)
            events.append(event)

        return events

    def _magnitude_to_severity(self, mag: float) -> Severity:
        """Convert Richter magnitude to severity level."""
        if mag >= 7.0:
            return Severity.CRITICAL
        elif mag >= 6.0:
            return Severity.HIGH
        elif mag >= 4.5:
            return Severity.MODERATE
        else:
            return Severity.LOW

    def _estimate_impact_radius(
        self, magnitude: float, depth_km: Optional[float] = None
    ) -> float:
        """
        USP: Estimate impact radius in km from magnitude and depth.
        Uses empirical formula: R ≈ 10^(0.5 * M - 1.8) for felt distance.
        Shallow quakes (<30 km) have larger surface impact.
        """
        import math
        base_radius = 10 ** (0.5 * magnitude - 1.8)
        if depth_km and depth_km < 30:
            # Shallow quakes amplify surface shaking
            depth_factor = 1.5
        elif depth_km and depth_km < 70:
            depth_factor = 1.0
        else:
            depth_factor = 0.7  # Deep quakes attenuate more
        return round(base_radius * depth_factor, 1)

    async def ingest(self) -> List[DisasterEvent]:
        """
        Main ingestion entry point.
        Returns list of earthquake DisasterEvents.
        """
        logger.info("[USGS] Starting earthquake ingestion...")
        raw_feeds = await self.fetch_all_feeds()

        all_events = []
        seen_ids = set()

        for feed in raw_feeds:
            events = self.parse_earthquakes(feed)
            for ev in events:
                if ev.event_id not in seen_ids:
                    all_events.append(ev)
                    seen_ids.add(ev.event_id)

        # Also fetch India-region earthquakes (broader net)
        async with aiohttp.ClientSession() as session:
            india_data = await self.fetch_by_region(
                session,
                min_lat=6.0, max_lat=37.0,
                min_lon=68.0, max_lon=98.0,
                min_magnitude=3.0, days_back=3,
            )
            if india_data:
                india_events = self.parse_earthquakes(india_data)
                for ev in india_events:
                    if ev.event_id not in seen_ids:
                        all_events.append(ev)
                        seen_ids.add(ev.event_id)

        logger.info(f"[USGS] Ingested {len(all_events)} earthquake events")
        return all_events
