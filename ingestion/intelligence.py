"""
Incident intelligence enrichment.

Adds verification status, concise summaries, and response-oriented guidance to
fused incidents so the product presents decisions rather than raw feeds.
"""

from __future__ import annotations

from typing import Dict, List


OFFICIAL_SOURCES = {"usgs", "gdacs", "imd", "open_meteo"}
HIGH_TRUST_SOURCES = {"usgs", "gdacs", "imd"}
SOCIAL_SOURCES = {"reddit", "news", "reliefweb"}


class IncidentIntelligenceEngine:
    def enrich(self, incidents: List[Dict]) -> List[Dict]:
        enriched = [self._enrich_incident(incident) for incident in incidents]
        enriched.sort(
            key=lambda item: (
                self._verification_rank(item["verification_status"]),
                item["priority_score"],
            ),
            reverse=True,
        )
        return enriched

    def _enrich_incident(self, incident: Dict) -> Dict:
        sources = set(incident.get("sources", []))
        severity = incident.get("severity", "moderate")
        confidence = float(incident.get("confidence_score", 0))
        source_count = int(incident.get("source_count", 0))
        event_count = int(incident.get("event_count", 0))

        has_official = bool(sources & OFFICIAL_SOURCES)
        has_high_trust = bool(sources & HIGH_TRUST_SOURCES)
        has_social = bool(sources & SOCIAL_SOURCES)

        verification_status = self._verification_status(
            severity=severity,
            confidence=confidence,
            source_count=source_count,
            has_official=has_official,
            has_high_trust=has_high_trust,
            has_social=has_social,
        )
        priority_bucket = self._priority_bucket(severity, confidence, source_count)
        recommended_actions = self._recommended_actions(
            incident["disaster_type"], severity, verification_status
        )
        ai_relevance_index = self._ai_relevance_index(incident, verification_status)
        verification_strength = self._verification_strength(
            confidence, source_count, has_official, has_social
        )
        impact_radius_km = self._impact_radius_estimate(incident)
        incident_summary = self._build_summary(
            incident=incident,
            verification_status=verification_status,
            event_count=event_count,
            source_count=source_count,
        )
        evidence = self._build_evidence(incident, has_official, has_social)

        updated = dict(incident)
        updated["verification_status"] = verification_status
        updated["priority_bucket"] = priority_bucket
        updated["incident_summary"] = incident_summary
        updated["recommended_actions"] = recommended_actions
        updated["evidence"] = evidence
        updated["ai_relevance_index"] = ai_relevance_index
        updated["verification_strength"] = verification_strength
        updated["impact_radius_km"] = impact_radius_km
        return updated

    def _verification_status(
        self,
        *,
        severity: str,
        confidence: float,
        source_count: int,
        has_official: bool,
        has_high_trust: bool,
        has_social: bool,
    ) -> str:
        if has_high_trust and source_count >= 2 and confidence >= 70:
            return "verified"
        if has_official and confidence >= 60:
            return "likely_verified"
        if has_social and source_count >= 2 and confidence >= 45:
            return "partially_corroborated"
        if severity in {"high", "critical"} and has_official:
            return "monitor_closely"
        return "unverified"

    def _priority_bucket(self, severity: str, confidence: float, source_count: int) -> str:
        if severity == "critical" and confidence >= 70:
            return "immediate"
        if severity in {"critical", "high"} and (confidence >= 55 or source_count >= 2):
            return "high"
        if confidence >= 45:
            return "medium"
        return "low"

    def _build_summary(
        self,
        *,
        incident: Dict,
        verification_status: str,
        event_count: int,
        source_count: int,
    ) -> str:
        location = incident.get("location", {}).get("name") or "an unknown location"
        disaster_type = incident.get("disaster_type", "incident")
        severity = incident.get("severity", "moderate")
        return (
            f"{severity.title()} {disaster_type} activity around {location}, "
            f"supported by {event_count} signals across {source_count} sources. "
            f"Current verification status: {verification_status.replace('_', ' ')}."
        )

    def _recommended_actions(
        self, disaster_type: str, severity: str, verification_status: str
    ) -> List[str]:
        actions = []
        if verification_status in {"verified", "likely_verified"}:
            actions.append("Escalate to operations dashboard and notify district leads.")
        elif verification_status == "partially_corroborated":
            actions.append("Cross-check with district authorities before dispatch decisions.")
        else:
            actions.append("Keep under watch until corroborated by official or field sources.")

        if disaster_type in {"flood", "storm", "cyclone"}:
            actions.append("Review rainfall, drainage, and evacuation-risk zones.")
        elif disaster_type == "earthquake":
            actions.append("Check impact radius, population centers, and aftershock advisories.")
        elif disaster_type == "wildfire":
            actions.append("Inspect spread direction, wind conditions, and nearby settlements.")
        else:
            actions.append("Review source evidence and local exposure before resource allocation.")

        if severity in {"high", "critical"}:
            actions.append("Prioritize for responder briefing in the next operational cycle.")
        return actions[:3]

    def _build_evidence(
        self, incident: Dict, has_official: bool, has_social: bool
    ) -> Dict:
        return {
            "sources": incident.get("sources", []),
            "has_official_source": has_official,
            "has_social_signal": has_social,
            "supporting_titles": incident.get("supporting_titles", []),
        }

    def _ai_relevance_index(self, incident: Dict, verification_status: str) -> int:
        confidence = float(incident.get("confidence_score", 0))
        freshness = float(incident.get("freshness_score", 0))
        source_count = int(incident.get("source_count", 0))
        event_count = int(incident.get("event_count", 0))
        priority = incident.get("priority_bucket", "medium")

        score = confidence * 0.5
        score += min(freshness * 30, 20)
        score += min(source_count * 6, 18)
        score += min(event_count * 1.5, 12)
        if verification_status in {"verified", "likely_verified"}:
            score += 10
        if priority == "immediate":
            score += 10
        elif priority == "high":
            score += 6
        return max(0, min(100, round(score)))

    def _verification_strength(
        self,
        confidence: float,
        source_count: int,
        has_official: bool,
        has_social: bool,
    ) -> str:
        if has_official and source_count >= 2 and confidence >= 70:
            return "strong"
        if has_official and confidence >= 55:
            return "moderate"
        if has_social and source_count >= 2:
            return "emerging"
        return "weak"

    def _impact_radius_estimate(self, incident: Dict) -> float | None:
        disaster_type = incident.get("disaster_type")
        severity = incident.get("severity")
        source_count = int(incident.get("source_count", 0))

        base_by_type = {
            "earthquake": 120,
            "flood": 40,
            "storm": 60,
            "cyclone": 140,
            "wildfire": 50,
            "landslide": 20,
            "heatwave": 80,
        }
        base = base_by_type.get(disaster_type, 35)
        severity_factor = {
            "critical": 1.6,
            "high": 1.25,
            "moderate": 1.0,
            "low": 0.7,
        }.get(severity, 1.0)
        source_factor = min(1 + (source_count - 1) * 0.08, 1.3)
        return round(base * severity_factor * source_factor, 1)

    def _verification_rank(self, status: str) -> int:
        order = {
            "verified": 5,
            "likely_verified": 4,
            "partially_corroborated": 3,
            "monitor_closely": 2,
            "unverified": 1,
        }
        return order.get(status, 0)
