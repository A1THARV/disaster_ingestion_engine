"""
DisasterIntel — GDACS (Global Disaster Alert and Coordination System) Ingestion
UN-backed platform covering earthquakes, cyclones, floods, volcanoes, wildfires.
Uses both REST API and RSS feed as fallback.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

from models.events import (
    DisasterEvent, DisasterType, GeoLocation, Severity, SourceType,
)
from config.settings import config

logger = logging.getLogger(__name__)

# GDACS event type mapping
GDACS_TYPE_MAP = {
    "EQ": DisasterType.EARTHQUAKE,
    "TC": DisasterType.CYCLONE,
    "FL": DisasterType.FLOOD,
    "VO": DisasterType.VOLCANO,
    "WF": DisasterType.WILDFIRE,
    "DR": DisasterType.DROUGHT,
    "TS": DisasterType.TSUNAMI,
}

GDACS_ALERT_SEVERITY = {
    "Red": Severity.CRITICAL,
    "Orange": Severity.HIGH,
    "Green": Severity.MODERATE,
}


class GDACSIngestor:
    """Fetches global disaster alerts from GDACS API and RSS feed."""

    def __init__(self):
        self.config = config.gdacs

    # ─── REST API Methods ────────────────────────────────────────────

    async def fetch_events_api(
        self, session: aiohttp.ClientSession,
        event_type: Optional[str] = None,
        alert_level: Optional[str] = None,
        limit: int = 50,
    ) -> Optional[Dict]:
        """Fetch events from GDACS REST API."""
        params = {"limit": limit}
        if event_type:
            params["eventtype"] = event_type
        if alert_level:
            params["alertlevel"] = alert_level

        try:
            async with session.get(
                self.config.events_list,
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"Accept": "application/json"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"[GDACS API] Fetched events (type={event_type})")
                    return data
                else:
                    logger.warning(f"[GDACS API] HTTP {resp.status}")
                    return None
        except Exception as e:
            logger.warning(f"[GDACS API] Error: {e}")
            return None

    async def fetch_event_detail(
        self, session: aiohttp.ClientSession,
        event_type: str, event_id: str,
    ) -> Optional[Dict]:
        """Fetch detailed data for a specific event."""
        params = {"eventtype": event_type, "eventid": event_id}
        try:
            async with session.get(
                self.config.event_data,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"Accept": "application/json"},
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logger.warning(f"[GDACS API] Detail fetch error: {e}")
            return None

    # ─── RSS Feed Methods (Reliable Fallback) ────────────────────────

    async def fetch_rss_feed(
        self, session: aiohttp.ClientSession
    ) -> Optional[str]:
        """Fetch GDACS RSS feed as XML string."""
        try:
            async with session.get(
                self.config.rss_feed,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    logger.info("[GDACS RSS] Fetched RSS feed")
                    return text
                else:
                    logger.error(f"[GDACS RSS] HTTP {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"[GDACS RSS] Error: {e}")
            return None

    def parse_rss_feed(self, xml_text: str) -> List[DisasterEvent]:
        """Parse GDACS RSS XML into DisasterEvents."""
        events = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f"[GDACS RSS] XML parse error: {e}")
            return events

        # GDACS RSS uses the 'gdacs' namespace
        ns = {
            "gdacs": "http://www.gdacs.org",
            "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
            "dc": "http://purl.org/dc/elements/1.1/",
        }

        channel = root.find("channel")
        if channel is None:
            return events

        for item in channel.findall("item"):
            try:
                event = self._parse_rss_item(item, ns)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning(f"[GDACS RSS] Error parsing item: {e}")
                continue

        return events

    def _parse_rss_item(self, item: ET.Element, ns: Dict) -> Optional[DisasterEvent]:
        """Parse a single RSS item into a DisasterEvent."""
        title = item.findtext("title", "")
        description = item.findtext("description", "")
        link = item.findtext("link", "")
        pub_date_str = item.findtext("pubDate", "")

        # GDACS-specific fields
        event_type = item.findtext("gdacs:eventtype", "", ns)
        alert_level = item.findtext("gdacs:alertlevel", "", ns)
        event_id = item.findtext("gdacs:eventid", "", ns)
        severity_text = item.findtext("gdacs:severity", "", ns)
        population = item.findtext("gdacs:population", "", ns)
        country = item.findtext("gdacs:country", "", ns)

        # Geographic coordinates
        lat_text = item.findtext("geo:lat", "0", ns)
        lon_text = item.findtext("geo:long", "0", ns)

        try:
            lat = float(lat_text) if lat_text else 0
            lon = float(lon_text) if lon_text else 0
        except ValueError:
            lat, lon = 0, 0

        # Parse timestamp
        timestamp = self._parse_rss_date(pub_date_str)

        # Map to our types
        disaster_type = GDACS_TYPE_MAP.get(event_type, DisasterType.UNKNOWN)
        severity = GDACS_ALERT_SEVERITY.get(alert_level, Severity.MODERATE)

        # Parse magnitude from severity text (e.g., "Magnitude 5.6")
        magnitude = self._extract_magnitude(severity_text)

        # Parse affected population
        affected_pop = self._parse_population(population)

        event = DisasterEvent(
            event_id=DisasterEvent.generate_id("gdacs", f"{event_type}_{event_id}"),
            source="gdacs",
            source_type=SourceType.GLOBAL_ALERT_SYSTEM,
            disaster_type=disaster_type,
            severity=severity,
            location=GeoLocation(
                latitude=lat,
                longitude=lon,
                location_name=country or title,
                country=country,
            ),
            timestamp=timestamp,
            title=f"[GDACS {alert_level}] {title}",
            description=description,
            magnitude=magnitude,
            affected_population=affected_pop,
            alert_level=alert_level,
            source_url=link,
            # GDACS is UN-backed — very high confidence
            confidence_score=90.0,
            raw_data={
                "event_type": event_type,
                "event_id": event_id,
                "alert_level": alert_level,
                "severity_text": severity_text,
                "population": population,
                "country": country,
            },
        )
        event.compute_freshness(config.scoring.freshness_half_life_minutes)
        return event

    def parse_api_events(self, api_response: Dict) -> List[DisasterEvent]:
        """Parse GDACS API JSON response into DisasterEvents."""
        events = []
        features = api_response.get("features", [])

        for feature in features:
            props = feature.get("properties", {})
            geom = feature.get("geometry", {})
            coords = geom.get("coordinates", [0, 0])

            event_type = props.get("eventtype", "")
            disaster_type = GDACS_TYPE_MAP.get(event_type, DisasterType.UNKNOWN)
            alert_level = props.get("alertlevel", "Green")
            severity = GDACS_ALERT_SEVERITY.get(alert_level, Severity.MODERATE)

            # Handle coordinates (GeoJSON: [lon, lat])
            lon = coords[0] if len(coords) > 0 else 0
            lat = coords[1] if len(coords) > 1 else 0

            # Parse dates
            from_date = props.get("fromdate", "")
            timestamp = self._parse_iso_date(from_date)

            event = DisasterEvent(
                event_id=DisasterEvent.generate_id(
                    "gdacs",
                    f"{event_type}_{props.get('eventid', '')}"
                ),
                source="gdacs",
                source_type=SourceType.GLOBAL_ALERT_SYSTEM,
                disaster_type=disaster_type,
                severity=severity,
                location=GeoLocation(
                    latitude=lat,
                    longitude=lon,
                    location_name=props.get("name", props.get("country", "")),
                    country=props.get("country", ""),
                ),
                timestamp=timestamp,
                title=f"[GDACS {alert_level}] {props.get('name', event_type)} in {props.get('country', 'Unknown')}",
                description=props.get("description", ""),
                alert_level=alert_level,
                affected_population=props.get("population_affected"),
                source_url=props.get("url"),
                confidence_score=90.0,
                raw_data=props,
            )
            event.compute_freshness(config.scoring.freshness_half_life_minutes)
            events.append(event)

        return events

    # ─── Utility Methods ─────────────────────────────────────────────

    def _parse_rss_date(self, date_str: str) -> datetime:
        """Parse RSS date format."""
        formats = [
            "%a, %d %b %Y %H:%M:%S %Z",
            "%a, %d %b %Y %H:%M:%S %z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except (ValueError, TypeError):
                continue
        return datetime.now(timezone.utc)

    def _parse_iso_date(self, date_str: str) -> datetime:
        """Parse ISO format date."""
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    def _extract_magnitude(self, severity_text: str) -> Optional[float]:
        """Extract numeric magnitude from GDACS severity text."""
        import re
        if not severity_text:
            return None
        match = re.search(r"(\d+\.?\d*)", severity_text)
        return float(match.group(1)) if match else None

    def _parse_population(self, pop_str: str) -> Optional[int]:
        """Parse population string to integer."""
        if not pop_str:
            return None
        import re
        # Remove non-numeric characters except digits
        cleaned = re.sub(r"[^\d]", "", pop_str)
        return int(cleaned) if cleaned else None

    # ─── Main Ingestion ──────────────────────────────────────────────

    async def ingest(self) -> List[DisasterEvent]:
        """
        Main ingestion entry point.
        Tries API first, falls back to RSS feed.
        Returns list of disaster events.
        """
        logger.info("[GDACS] Starting disaster alert ingestion...")
        all_events = []
        seen_ids = set()

        async with aiohttp.ClientSession() as session:
            # Strategy 1: Try REST API for each event type
            api_success = False
            for event_type in self.config.event_types:
                api_data = await self.fetch_events_api(
                    session, event_type=event_type
                )
                if api_data:
                    api_success = True
                    events = self.parse_api_events(api_data)
                    for ev in events:
                        if ev.event_id not in seen_ids:
                            all_events.append(ev)
                            seen_ids.add(ev.event_id)

            # Strategy 2: RSS feed as fallback (or supplement)
            rss_xml = await self.fetch_rss_feed(session)
            if rss_xml:
                rss_events = self.parse_rss_feed(rss_xml)
                for ev in rss_events:
                    if ev.event_id not in seen_ids:
                        all_events.append(ev)
                        seen_ids.add(ev.event_id)

        logger.info(f"[GDACS] Ingested {len(all_events)} disaster alerts")
        return all_events
