"""
NLP utilities for disaster text classification.

Supports transformer-backed classification when a compatible model is
available locally or via environment configuration, and falls back to a
rule-based classifier for offline development.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional

from models.events import DisasterType


DEFAULT_LABEL_MAPPING = {
    "flood": DisasterType.FLOOD,
    "earthquake": DisasterType.EARTHQUAKE,
    "cyclone": DisasterType.CYCLONE,
    "wildfire": DisasterType.WILDFIRE,
    "landslide": DisasterType.LANDSLIDE,
    "tsunami": DisasterType.TSUNAMI,
    "storm": DisasterType.STORM,
    "heatwave": DisasterType.HEATWAVE,
}

BINARY_POSITIVE_LABELS = {
    "1",
    "yes",
    "true",
    "positive",
    "disaster",
    "relevant",
    "label_1",
}

KEYWORD_PATTERNS = {
    DisasterType.FLOOD: [r"\bflood", r"\bwaterlog", r"\bflash flood"],
    DisasterType.EARTHQUAKE: [r"\bearthquake", r"\bquake", r"\btremor"],
    DisasterType.CYCLONE: [r"\bcyclone", r"\bhurricane", r"\btyphoon"],
    DisasterType.WILDFIRE: [r"\bwildfire", r"\bforest fire", r"\bblaze"],
    DisasterType.LANDSLIDE: [r"\blandslide", r"\bmudslide"],
    DisasterType.TSUNAMI: [r"\btsunami"],
    DisasterType.STORM: [r"\bstorm", r"\bthunderstorm", r"\blightning"],
    DisasterType.HEATWAVE: [r"\bheatwave", r"\bheat wave", r"\bextreme heat"],
}


@dataclass
class DisasterClassification:
    is_disaster: bool
    disaster_type: Optional[DisasterType]
    confidence: float
    provider: str
    sentiment: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "is_disaster": self.is_disaster,
            "disaster_type": self.disaster_type.value if self.disaster_type else None,
            "confidence": self.confidence,
            "provider": self.provider,
            "sentiment": self.sentiment,
        }


@dataclass
class SentimentResult:
    label: str
    confidence: float
    provider: str

    def to_dict(self) -> Dict:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "provider": self.provider,
        }


class DisasterTextClassifier:
    """
    Transformer-backed classifier with a safe offline fallback.

    Environment variables:
    - DISASTER_TEXT_MODEL_ID: Hugging Face model id or local path
    - DISASTER_TEXT_LABEL_MAPPING: JSON object mapping model labels to disaster types
    - DISTILBERT_MODEL_ID / CRISISBERT_MODEL_ID: optional aliases
    """

    def __init__(self):
        self.model_id = (
            os.getenv("CRISISBERT_MODEL_ID")
            or os.getenv("DISTILBERT_MODEL_ID")
            or os.getenv("DISASTER_TEXT_MODEL_ID")
            or "elam2909/bert-disaster-classifier"
        )
        self.label_mapping = self._load_label_mapping()
        self._pipeline = None
        self._pipeline_error: Optional[str] = None

    def classify(self, text: str) -> DisasterClassification:
        text = (text or "").strip()
        if not text:
            return DisasterClassification(
                is_disaster=False,
                disaster_type=None,
                confidence=0.0,
                provider="empty",
            )

        transformer_result = self._classify_with_transformer(text)
        if transformer_result:
            return transformer_result
        return self._classify_with_rules(text)

    def _classify_with_transformer(
        self, text: str
    ) -> Optional[DisasterClassification]:
        if not self.model_id:
            return None

        if self._pipeline_error:
            return None

        if self._pipeline is None:
            try:
                from transformers import pipeline

                self._pipeline = pipeline(
                    "text-classification",
                    model=self.model_id,
                    tokenizer=self.model_id,
                )
            except Exception as exc:
                self._pipeline_error = str(exc)
                return None

        try:
            result = self._pipeline(text, truncation=True)[0]
        except Exception as exc:
            self._pipeline_error = str(exc)
            return None

        label = str(result.get("label", "")).lower()
        score = float(result.get("score", 0.0))
        disaster_type = self.label_mapping.get(label)
        is_disaster = disaster_type is not None or self._is_positive_disaster_label(label)

        if is_disaster and disaster_type is None:
            disaster_type = self._infer_disaster_subtype(text)

        return DisasterClassification(
            is_disaster=is_disaster,
            disaster_type=disaster_type,
            confidence=round(score, 4),
            provider=self.model_id,
        )

    def _classify_with_rules(self, text: str) -> DisasterClassification:
        disaster_type = self._infer_disaster_subtype(text)
        if disaster_type is not None:
            return DisasterClassification(
                is_disaster=True,
                disaster_type=disaster_type,
                confidence=round(max(_compute_relevance_score(text), 0.55), 4),
                provider="rule_based_fallback",
            )

        return DisasterClassification(
            is_disaster=False,
            disaster_type=None,
            confidence=round(_compute_relevance_score(text), 4),
            provider="rule_based_fallback",
        )

    def _load_label_mapping(self) -> Dict[str, DisasterType]:
        mapping = dict(DEFAULT_LABEL_MAPPING)
        raw = os.getenv("DISASTER_TEXT_LABEL_MAPPING")
        if not raw:
            return mapping

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return mapping

        for label, disaster_name in parsed.items():
            normalized = str(disaster_name).lower()
            if normalized in DisasterType._value2member_map_:
                mapping[str(label).lower()] = DisasterType(normalized)
        return mapping

    def _infer_disaster_subtype(self, text: str) -> Optional[DisasterType]:
        lowered = text.lower()
        best_type: Optional[DisasterType] = None
        best_matches = 0

        for disaster_type, patterns in KEYWORD_PATTERNS.items():
            matches = sum(len(re.findall(pattern, lowered)) for pattern in patterns)
            if matches > best_matches:
                best_matches = matches
                best_type = disaster_type
        return best_type

    def _is_positive_disaster_label(self, label: str) -> bool:
        normalized = label.strip().lower()
        return normalized in BINARY_POSITIVE_LABELS or normalized.endswith("_1")

    def infer_disaster_subtype(self, text: str) -> Optional[DisasterType]:
        return self._infer_disaster_subtype(text)


class SentimentAnalyzer:
    def __init__(self):
        self.model_id = os.getenv("SENTIMENT_MODEL_ID", "")
        self._pipeline = None
        self._pipeline_error: Optional[str] = None

    def analyze(self, text: str) -> SentimentResult:
        text = (text or "").strip()
        if not text:
            return SentimentResult(label="neutral", confidence=0.0, provider="empty")

        transformer = self._analyze_with_transformer(text)
        if transformer:
            return transformer
        return self._analyze_with_rules(text)

    def _analyze_with_transformer(self, text: str) -> Optional[SentimentResult]:
        if not self.model_id or self._pipeline_error:
            return None
        if self._pipeline is None:
            try:
                from transformers import pipeline

                self._pipeline = pipeline(
                    "text-classification",
                    model=self.model_id,
                    tokenizer=self.model_id,
                )
            except Exception as exc:
                self._pipeline_error = str(exc)
                return None
        try:
            result = self._pipeline(text, truncation=True)[0]
        except Exception as exc:
            self._pipeline_error = str(exc)
            return None
        return SentimentResult(
            label=str(result.get("label", "neutral")).lower(),
            confidence=round(float(result.get("score", 0.0)), 4),
            provider=self.model_id,
        )

    def _analyze_with_rules(self, text: str) -> SentimentResult:
        lowered = text.lower()
        negative_terms = [
            "dead", "killed", "injured", "evacuation", "collapse",
            "emergency", "critical", "flooded", "destroyed", "panic",
        ]
        positive_terms = ["safe", "contained", "restored", "stable", "cleared"]
        negative_hits = sum(term in lowered for term in negative_terms)
        positive_hits = sum(term in lowered for term in positive_terms)

        if negative_hits > positive_hits:
            return SentimentResult("negative", min(0.5 + negative_hits * 0.08, 0.95), "rule_based_fallback")
        if positive_hits > negative_hits:
            return SentimentResult("positive", min(0.5 + positive_hits * 0.08, 0.95), "rule_based_fallback")
        return SentimentResult("neutral", 0.55, "rule_based_fallback")


@lru_cache(maxsize=1)
def get_disaster_classifier() -> DisasterTextClassifier:
    return DisasterTextClassifier()


@lru_cache(maxsize=1)
def get_sentiment_analyzer() -> SentimentAnalyzer:
    return SentimentAnalyzer()


def _compute_relevance_score(text: str) -> float:
    lowered = text.lower()
    words = lowered.split()
    if not words:
        return 0.0

    match_count = 0
    for patterns in KEYWORD_PATTERNS.values():
        for pattern in patterns:
            match_count += len(re.findall(pattern, lowered))
    return min(match_count / len(words) * 5, 1.0)
