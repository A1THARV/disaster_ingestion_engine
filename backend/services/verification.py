"""Incident-linked claim verification using Firecrawl search and NLP."""

from __future__ import annotations

from typing import Dict, List

import httpx

from config.settings import config
from ingestion.nlp import get_disaster_classifier, get_sentiment_analyzer


class IncidentVerificationService:
    def __init__(self):
        self.classifier = get_disaster_classifier()
        self.sentiment = get_sentiment_analyzer()
        self.api_key = config.firecrawl.api_key
        self.search_url = config.firecrawl.search_url

    def verify_incident(self, incident: Dict) -> Dict:
        query = self._build_query(incident)
        evidence = self._search_evidence(query)
        analyzed = [self._analyze_evidence(item, incident) for item in evidence]
        verdict = self._build_verdict(analyzed)
        return {
            "incident_id": incident.get("incident_id"),
            "query": query,
            "verdict": verdict,
            "evidence": analyzed,
        }

    def _build_query(self, incident: Dict) -> str:
        location = incident.get("location", {}).get("name", "")
        disaster_type = incident.get("disaster_type", "")
        title = incident.get("title", "")
        support = " ".join(incident.get("supporting_titles", [])[:2])
        return f"{disaster_type} {location} {title} {support}".strip()

    def _search_evidence(self, query: str) -> List[Dict]:
        if not self.api_key:
            return []
        payload = {
            "query": query,
            "limit": 5,
            "sources": ["web"],
            "scrapeOptions": {
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(self.search_url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return [{"title": "Search failed", "url": "", "markdown": str(exc), "description": ""}]

        web_results = data.get("data", {}).get("web", [])
        return web_results if isinstance(web_results, list) else []

    def _analyze_evidence(self, item: Dict, incident: Dict) -> Dict:
        text = " ".join(
            [
                item.get("title", ""),
                item.get("description", ""),
                item.get("markdown", "")[:1500],
            ]
        ).strip()
        classification = self.classifier.classify(text)
        sentiment = self.sentiment.analyze(text)
        stance = self._stance(text, incident)

        return {
            "title": item.get("title"),
            "url": item.get("url"),
            "stance": stance,
            "sentiment": sentiment.to_dict(),
            "classification": classification.to_dict(),
            "snippet": text[:320],
        }

    def _stance(self, text: str, incident: Dict) -> str:
        lowered = text.lower()
        disaster_type = incident.get("disaster_type", "")
        location = (incident.get("location", {}) or {}).get("name", "").lower()

        refute_terms = ["fake", "false", "hoax", "misleading", "not true", "debunk"]
        support_terms = [disaster_type.lower(), location]

        if any(term and term in lowered for term in refute_terms):
            return "refuting"
        if all(term for term in support_terms) and all(term in lowered for term in support_terms):
            return "supporting"
        if disaster_type.lower() in lowered or location in lowered:
            return "related"
        return "unclear"

    def _build_verdict(self, evidence: List[Dict]) -> Dict:
        supporting = len([item for item in evidence if item["stance"] == "supporting"])
        refuting = len([item for item in evidence if item["stance"] == "refuting"])
        related = len([item for item in evidence if item["stance"] == "related"])

        if supporting >= 2 and refuting == 0:
            label = "externally_corroborated"
        elif refuting > supporting:
            label = "possible_misinformation"
        elif supporting >= 1 or related >= 2:
            label = "needs_review"
        else:
            label = "insufficient_external_evidence"

        return {
            "label": label,
            "supporting_count": supporting,
            "refuting_count": refuting,
            "related_count": related,
        }
