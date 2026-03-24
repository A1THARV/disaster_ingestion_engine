"""
DisasterIntel — Cross-Source Verification Engine (USP #1)
The core differentiator: verifies information across multiple independent sources
to compute a confidence score and detect potential misinformation.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from models.events import (
    DisasterEvent, DisasterType, GeoLocation, Severity,
    SocialPost, WeatherSnapshot,
)
from config.settings import config

logger = logging.getLogger(__name__)


class CrossSourceVerifier:
    """
    Verifies disaster events by cross-referencing across data sources.

    The idea: A flood report from social media is much more credible if
    the weather data shows heavy rainfall in the same area at the same time.
    An earthquake tweet is verified if USGS confirms seismic activity nearby.

    This is what transforms raw data into INTELLIGENCE.
    """

    def __init__(self):
        self.scoring = config.scoring
        # Spatial threshold: how close (km) two data points must be to correlate
        self.spatial_threshold_km = 100.0
        # Temporal threshold: how close in time (hours) to correlate
        self.temporal_threshold_hours = 6.0

    def verify_events(
        self,
        events: List[DisasterEvent],
        weather_snapshots: List[WeatherSnapshot],
        social_posts: List[SocialPost],
    ) -> List[DisasterEvent]:
        """
        Main verification loop.
        For each event, check if other sources corroborate it.
        Returns events with updated confidence scores.
        """
        logger.info(
            f"[CrossVerify] Verifying {len(events)} events against "
            f"{len(weather_snapshots)} weather points and "
            f"{len(social_posts)} social posts"
        )

        for event in events:
            corroborations = []

            # 1. Check weather data corroboration
            weather_match = self._check_weather_corroboration(
                event, weather_snapshots
            )
            if weather_match:
                corroborations.append(weather_match)

            # 2. Check social media corroboration
            social_matches = self._check_social_corroboration(
                event, social_posts
            )
            corroborations.extend(social_matches)

            # 3. Check other disaster events for corroboration
            other_event_matches = self._check_event_corroboration(
                event, [e for e in events if e.event_id != event.event_id]
            )
            corroborations.extend(other_event_matches)

            # Update confidence score based on corroborations
            event.confidence_score = self._compute_confidence(
                event, corroborations
            )
            event.corroborating_sources = [c["source"] for c in corroborations]

            # Refresh freshness
            event.compute_freshness(self.scoring.freshness_half_life_minutes)

        # Sort by confidence * freshness (most reliable & recent first)
        events.sort(
            key=lambda e: e.confidence_score * e.freshness_score,
            reverse=True,
        )

        logger.info("[CrossVerify] Verification complete")
        return events

    def _check_weather_corroboration(
        self,
        event: DisasterEvent,
        snapshots: List[WeatherSnapshot],
    ) -> Optional[Dict]:
        """
        Check if weather data supports this event.
        E.g., flood event + heavy rain nearby = corroborated.
        """
        if event.disaster_type not in (
            DisasterType.FLOOD, DisasterType.STORM,
            DisasterType.CYCLONE, DisasterType.LANDSLIDE,
        ):
            return None  # Weather irrelevant for earthquakes, volcanoes

        for snap in snapshots:
            # Check spatial proximity
            distance = event.location.distance_to(snap.location)
            if distance > self.spatial_threshold_km:
                continue

            # Check temporal proximity
            if snap.timestamp and event.timestamp:
                try:
                    time_diff = abs(
                        (snap.timestamp - event.timestamp).total_seconds()
                    ) / 3600
                except TypeError:
                    # Mixed tz-aware and naive datetimes — skip comparison
                    continue
                if time_diff > self.temporal_threshold_hours:
                    continue

            # Check if weather conditions match the disaster type
            if self._weather_supports_event(event.disaster_type, snap):
                return {
                    "source": f"weather_{snap.source}",
                    "type": "weather_corroboration",
                    "distance_km": round(distance, 1),
                    "details": {
                        "precipitation_mm": snap.precipitation_mm,
                        "wind_speed_kmh": snap.wind_speed_kmh,
                        "weather_code": snap.weather_code,
                        "location": snap.location.location_name,
                    },
                    "weight": self.scoring.source_weights.get(
                        snap.source, 0.5
                    ),
                }
        return None

    def _weather_supports_event(
        self, disaster_type: DisasterType, snap: WeatherSnapshot
    ) -> bool:
        """Check if weather snapshot supports the claimed disaster type."""
        if disaster_type == DisasterType.FLOOD:
            return (
                (snap.precipitation_mm or 0) > 15  # Moderate+ rain
                or (snap.rain_mm or 0) > 15
                or (snap.soil_moisture or 0) > 0.35  # Saturated ground
            )
        elif disaster_type == DisasterType.STORM:
            return (
                (snap.wind_speed_kmh or 0) > 50
                or (snap.cape_jkg or 0) > 750  # Thunderstorm potential
                or (snap.weather_code or 0) >= 80  # Rain showers or worse
            )
        elif disaster_type == DisasterType.CYCLONE:
            return (
                (snap.wind_speed_kmh or 0) > 90
                or (snap.wind_gusts_kmh or 0) > 120
            )
        elif disaster_type == DisasterType.LANDSLIDE:
            return (
                (snap.precipitation_mm or 0) > 30
                and (snap.soil_moisture or 0) > 0.4
            )
        return False

    def _check_social_corroboration(
        self,
        event: DisasterEvent,
        posts: List[SocialPost],
    ) -> List[Dict]:
        """
        Check if social media posts corroborate this event.
        Multiple posts about the same event = higher confidence.
        """
        corroborations = []

        for post in posts:
            if not post.is_disaster_related:
                continue

            # Check disaster type match
            if post.disaster_type and post.disaster_type != event.disaster_type:
                continue

            # Check location overlap
            location_match = self._locations_overlap(
                event, post.extracted_locations
            )
            if not location_match:
                continue

            # Check temporal proximity
            if post.timestamp and event.timestamp:
                try:
                    time_diff = abs(
                        (post.timestamp - event.timestamp).total_seconds()
                    ) / 3600
                except TypeError:
                    continue
                if time_diff > self.temporal_threshold_hours:
                    continue

            corroborations.append({
                "source": f"social_{post.source_platform}",
                "type": "social_corroboration",
                "post_id": post.post_id,
                "credibility": post.credibility_score,
                "details": {
                    "content_preview": post.content[:100],
                    "platform": post.source_platform,
                    "locations": post.extracted_locations,
                },
                "weight": (
                    self.scoring.source_weights.get(
                        post.source_platform, 0.3
                    )
                    * post.credibility_score
                ),
            })

        return corroborations

    def _check_event_corroboration(
        self,
        event: DisasterEvent,
        other_events: List[DisasterEvent],
    ) -> List[Dict]:
        """Check if other disaster events corroborate this one."""
        corroborations = []

        for other in other_events:
            if other.disaster_type != event.disaster_type:
                continue
            if other.source == event.source:
                continue  # Same source doesn't count as corroboration

            # Spatial check
            distance = event.location.distance_to(other.location)
            if distance > self.spatial_threshold_km:
                continue

            # Temporal check
            try:
                time_diff = abs(
                    (other.timestamp - event.timestamp).total_seconds()
                ) / 3600
            except TypeError:
                continue
            if time_diff > self.temporal_threshold_hours:
                continue

            corroborations.append({
                "source": other.source,
                "type": "event_corroboration",
                "event_id": other.event_id,
                "distance_km": round(distance, 1),
                "weight": self.scoring.source_weights.get(other.source, 0.5),
            })

        return corroborations

    def _locations_overlap(
        self,
        event: DisasterEvent,
        post_locations: List[str],
    ) -> bool:
        """Check if event location overlaps with extracted post locations."""
        if not post_locations:
            return False  # No location info — can't corroborate spatially

        event_name = (event.location.location_name or "").lower()
        event_region = (event.location.region or "").lower()
        event_country = (event.location.country or "").lower()

        for loc in post_locations:
            loc_lower = loc.lower()
            if (
                loc_lower in event_name
                or loc_lower in event_region
                or loc_lower in event_country
                or event_name in loc_lower
            ):
                return True
        return False

    def _compute_confidence(
        self,
        event: DisasterEvent,
        corroborations: List[Dict],
    ) -> float:
        """
        Compute final confidence score (0-100).

        Formula:
        - Start with base confidence from source trustworthiness
        - Add weighted bonus for each corroborating source
        - More independent sources = higher confidence
        - Cap at 100
        """
        # Base confidence from the event's own source
        base_weight = self.scoring.source_weights.get(event.source, 0.5)
        base_confidence = base_weight * 60  # Max 60 from source alone

        if not corroborations:
            return min(base_confidence, event.confidence_score)

        # Corroboration bonus
        corroboration_bonus = 0
        unique_sources = set()

        for c in corroborations:
            source_key = c["source"].split("_")[0]
            if source_key not in unique_sources:
                unique_sources.add(source_key)
                corroboration_bonus += c["weight"] * 20  # Up to 20 per source

        # Diversity bonus: more independent source types = more reliable
        diversity_bonus = min(len(unique_sources) * 5, 20)

        total = base_confidence + corroboration_bonus + diversity_bonus
        return min(round(total, 1), 100.0)


class TemporalAnomalyDetector:
    """
    USP #2: Detects when multiple data sources spike simultaneously.
    Generates "Correlated Event Alerts" when anomalous patterns align.
    """

    def __init__(self, window_minutes: int = 30):
        self.window_minutes = window_minutes

    def detect_correlated_spikes(
        self,
        events: List[DisasterEvent],
        weather_snapshots: List[WeatherSnapshot],
        social_posts: List[SocialPost],
    ) -> List[Dict]:
        """
        Look for time windows where multiple sources show simultaneous spikes.
        Returns correlated alert objects.
        """
        alerts = []

        # Group events by location (nearest city)
        location_groups = self._group_by_location(events)

        for location_name, loc_events in location_groups.items():
            # Check if there's a temporal cluster
            time_windows = self._find_temporal_clusters(loc_events)

            for window_start, window_events in time_windows:
                # Count distinct source types
                source_types = set(e.source for e in window_events)

                if len(source_types) >= 2:
                    # Multi-source spike detected
                    dominant_type = self._get_dominant_disaster_type(
                        window_events
                    )
                    max_severity = max(
                        window_events,
                        key=lambda e: list(Severity).index(e.severity),
                    ).severity

                    alert = {
                        "type": "CORRELATED_EVENT_ALERT",
                        "location": location_name,
                        "disaster_type": dominant_type.value,
                        "severity": max_severity.value,
                        "window_start": window_start.isoformat(),
                        "window_minutes": self.window_minutes,
                        "source_count": len(source_types),
                        "sources": list(source_types),
                        "event_count": len(window_events),
                        "events": [
                            {
                                "id": e.event_id,
                                "source": e.source,
                                "title": e.title,
                                "confidence": e.confidence_score,
                            }
                            for e in window_events
                        ],
                    }
                    alerts.append(alert)

                    logger.info(
                        f"[AnomalyDetector] Correlated spike: "
                        f"{dominant_type.value} in {location_name} "
                        f"({len(source_types)} sources, {len(window_events)} events)"
                    )

        return alerts

    def _group_by_location(
        self, events: List[DisasterEvent]
    ) -> Dict[str, List[DisasterEvent]]:
        """Group events by approximate location (100km clusters)."""
        groups: Dict[str, List[DisasterEvent]] = {}
        for event in events:
            key = event.location.location_name or "unknown"
            if key not in groups:
                groups[key] = []
            groups[key].append(event)
        return groups

    def _find_temporal_clusters(
        self, events: List[DisasterEvent]
    ) -> List[Tuple[datetime, List[DisasterEvent]]]:
        """Find clusters of events within the time window."""
        if not events:
            return []

        sorted_events = sorted(events, key=lambda e: e.timestamp)
        clusters = []
        current_cluster = [sorted_events[0]]
        window_start = sorted_events[0].timestamp

        for event in sorted_events[1:]:
            time_diff = (event.timestamp - window_start).total_seconds() / 60
            if time_diff <= self.window_minutes:
                current_cluster.append(event)
            else:
                if len(current_cluster) >= 2:
                    clusters.append((window_start, current_cluster))
                current_cluster = [event]
                window_start = event.timestamp

        if len(current_cluster) >= 2:
            clusters.append((window_start, current_cluster))

        return clusters

    def _get_dominant_disaster_type(
        self, events: List[DisasterEvent]
    ) -> DisasterType:
        """Get the most common disaster type in a group of events."""
        type_counts: Dict[DisasterType, int] = {}
        for event in events:
            type_counts[event.disaster_type] = (
                type_counts.get(event.disaster_type, 0) + 1
            )
        return max(type_counts, key=type_counts.get)
