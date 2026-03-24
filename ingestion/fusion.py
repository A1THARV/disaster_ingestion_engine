"""
Incident fusion layer.

Transforms many raw source events into a smaller set of fused incidents that are
more useful for responders and UI consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List

from models.events import DisasterEvent, Severity


SEVERITY_ORDER = {
    Severity.LOW: 0,
    Severity.MODERATE: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


@dataclass
class IncidentCluster:
    disaster_type: str
    location_key: str
    seed: DisasterEvent
    events: List[DisasterEvent]


class IncidentFusionEngine:
    """
    Merge repeated raw events into operational incidents.

    Strategy:
    - same disaster type
    - same named location
    - within a configurable time window
    """

    def __init__(self, time_window_hours: float = 6.0):
        self.time_window_hours = time_window_hours

    def fuse(self, events: List[DisasterEvent]) -> List[Dict]:
        if not events:
            return []

        clusters: List[IncidentCluster] = []
        for event in sorted(events, key=lambda item: self._normalize_timestamp(item.timestamp)):
            cluster = self._find_cluster(clusters, event)
            if cluster is None:
                clusters.append(
                    IncidentCluster(
                        disaster_type=event.disaster_type.value,
                        location_key=self._location_key(event),
                        seed=event,
                        events=[event],
                    )
                )
            else:
                cluster.events.append(event)

        incidents = [self._build_incident(cluster) for cluster in clusters]
        incidents.sort(
            key=lambda item: (
                item["priority_score"],
                item["latest_timestamp"],
            ),
            reverse=True,
        )
        return incidents

    def _find_cluster(
        self, clusters: List[IncidentCluster], event: DisasterEvent
    ) -> IncidentCluster | None:
        for cluster in clusters:
            if cluster.disaster_type != event.disaster_type.value:
                continue
            if cluster.location_key != self._location_key(event):
                continue

            latest = max(item.timestamp for item in cluster.events)
            time_diff = abs(
                (
                    self._normalize_timestamp(event.timestamp)
                    - self._normalize_timestamp(latest)
                ).total_seconds()
            ) / 3600
            if time_diff <= self.time_window_hours:
                return cluster
        return None

    def _build_incident(self, cluster: IncidentCluster) -> Dict:
        events = cluster.events
        seed = cluster.seed
        latest_event = max(events, key=lambda item: self._normalize_timestamp(item.timestamp))
        strongest_event = max(events, key=lambda item: SEVERITY_ORDER[item.severity])
        unique_sources = sorted({event.source for event in events})
        avg_confidence = round(
            sum(event.confidence_score for event in events) / len(events), 1
        )
        avg_freshness = round(
            sum(event.freshness_score for event in events) / len(events), 3
        )

        supporting_titles = []
        seen_titles = set()
        for event in events:
            if event.title not in seen_titles:
                supporting_titles.append(event.title)
                seen_titles.add(event.title)

        return {
            "incident_id": seed.event_id,
            "title": self._incident_title(strongest_event, len(events), len(unique_sources)),
            "disaster_type": strongest_event.disaster_type.value,
            "severity": strongest_event.severity.value,
            "location": strongest_event.location.to_dict(),
            "source_count": len(unique_sources),
            "sources": unique_sources,
            "event_count": len(events),
            "confidence_score": avg_confidence,
            "freshness_score": avg_freshness,
            "first_timestamp": min(
                self._normalize_timestamp(event.timestamp) for event in events
            ).isoformat(),
            "latest_timestamp": self._normalize_timestamp(
                latest_event.timestamp
            ).isoformat(),
            "supporting_titles": supporting_titles[:5],
            "raw_event_ids": [event.event_id for event in events],
            "priority_score": round(
                avg_confidence
                + (SEVERITY_ORDER[strongest_event.severity] * 10)
                + min(len(unique_sources) * 4, 12)
                + min(len(events), 10),
                1,
            ),
        }

    def _incident_title(
        self, event: DisasterEvent, event_count: int, source_count: int
    ) -> str:
        location_name = event.location.location_name or "Unknown location"
        return (
            f"{event.disaster_type.value.title()} incident in {location_name} "
            f"({event_count} signals, {source_count} sources)"
        )

    def _location_key(self, event: DisasterEvent) -> str:
        location = event.location.location_name or "unknown"
        return location.strip().lower()

    def _normalize_timestamp(self, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)
