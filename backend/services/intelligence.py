"""Utilities for loading and shaping processed intelligence data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


class IntelligenceRepository:
    def __init__(self, processed_dir: str = "data/processed"):
        self.processed_dir = Path(processed_dir)
        self.latest_file = self.processed_dir / "latest_intelligence.json"

    def load_latest(self) -> Dict:
        if not self.latest_file.exists():
            return self._empty_payload()

        with open(self.latest_file, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def get_events(self) -> List[Dict]:
        payload = self.load_latest()
        return payload.get("events", [])

    def get_incidents(self) -> List[Dict]:
        payload = self.load_latest()
        return payload.get("incidents", [])

    def get_priority_incidents(self) -> List[Dict]:
        payload = self.load_latest()
        return payload.get("priority_incidents", [])

    def get_incident_by_id(self, incident_id: str) -> Dict | None:
        for incident in self.get_incidents():
            if incident.get("incident_id") == incident_id:
                return incident
        return None

    def get_timeline(self) -> List[Dict]:
        payload = self.load_latest()
        return payload.get("timeline", [])

    def get_map_layers(self) -> Dict:
        payload = self.load_latest()
        events = payload.get("events", [])
        incidents = payload.get("incidents", [])
        severe_weather = payload.get("severe_weather", [])
        correlated = payload.get("correlated_alerts", [])
        location_index = self._index_event_locations(events)

        markers = [
            {
                "id": incident.get("incident_id"),
                "title": incident.get("title"),
                "severity": incident.get("severity"),
                "disaster_type": incident.get("disaster_type"),
                "confidence_score": incident.get("confidence_score"),
                "source": ", ".join(incident.get("sources", [])),
                "timestamp": incident.get("latest_timestamp"),
                "location": incident.get("location", {}),
                "event_count": incident.get("event_count", 0),
                "source_count": incident.get("source_count", 0),
                "verification_status": incident.get("verification_status"),
                "priority_bucket": incident.get("priority_bucket"),
                "incident_summary": incident.get("incident_summary"),
                "ai_relevance_index": incident.get("ai_relevance_index"),
                "verification_strength": incident.get("verification_strength"),
                "impact_radius_km": incident.get("impact_radius_km"),
            }
            for incident in incidents
            if incident.get("location", {}).get("latitude") is not None
            and incident.get("location", {}).get("longitude") is not None
        ]

        weather_overlay = [
            {
                "id": f"weather-{index}",
                "title": snap.get("location", {}).get("name") or "Severe weather",
                "severity": snap.get("severity"),
                "precipitation_mm": snap.get("precipitation_mm"),
                "wind_speed_kmh": snap.get("wind_speed_kmh"),
                "location": snap.get("location", {}),
            }
            for index, snap in enumerate(severe_weather)
        ]

        correlated_overlay = [
            {
                "id": f"correlated-{index}",
                "location": item.get("location"),
                "disaster_type": item.get("disaster_type"),
                "severity": item.get("severity"),
                "sources": item.get("sources", []),
                "event_count": item.get("event_count", 0),
                "location_coords": location_index.get(item.get("location", "")),
            }
            for index, item in enumerate(correlated)
        ]

        return {
            "event_markers": markers,
            "weather_overlay": weather_overlay,
            "correlated_overlay": correlated_overlay,
        }

    def _empty_payload(self) -> Dict:
        return {
            "metadata": {},
            "summary": {},
            "ingestion": {"sources": [], "success_rate": 0.0},
            "correlated_alerts": [],
            "events": [],
            "incidents": [],
            "priority_incidents": [],
            "timeline": [],
            "severe_weather": [],
            "social_intelligence": {"total_posts": 0, "disaster_related": 0, "top_posts": []},
        }

    def _index_event_locations(self, events: List[Dict]) -> Dict[str, Dict]:
        index = {}
        for event in events:
            location = event.get("location", {})
            name = location.get("name")
            lat = location.get("latitude")
            lon = location.get("longitude")
            if name and lat is not None and lon is not None:
                index[name] = {"latitude": lat, "longitude": lon}
        return index
