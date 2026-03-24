"""Backend-facing NLP service wrappers."""

from __future__ import annotations

from typing import Dict

from ingestion.nlp import get_disaster_classifier, get_sentiment_analyzer


class NlpService:
    def __init__(self):
        self.classifier = get_disaster_classifier()
        self.sentiment = get_sentiment_analyzer()

    def classify_text(self, text: str) -> Dict:
        return {
            "classification": self.classifier.classify(text).to_dict(),
            "sentiment": self.sentiment.analyze(text).to_dict(),
        }
