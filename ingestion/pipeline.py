"""
DisasterIntel main ingestion pipeline orchestrator.
Coordinates data sources, cross-verification, and unified output artifacts.
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import config
from ingestion.cross_verify import CrossSourceVerifier, TemporalAnomalyDetector
from ingestion.fusion import IncidentFusionEngine
from ingestion.intelligence import IncidentIntelligenceEngine
from ingestion.sources.gdacs_alerts import GDACSIngestor
from ingestion.sources.imd_weather import IMDIngestor
from ingestion.sources.open_meteo import OpenMeteoIngestor
from ingestion.sources.social_news import FirecrawlNewsIngestor, RedditIngestor
from ingestion.sources.usgs_earthquake import USGSIngestor
from models.events import DisasterEvent, SocialPost, WeatherSnapshot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class SourceRunResult:
    """Structured result for one source ingestion run."""

    name: str
    ok: bool
    duration_seconds: float
    item_count: int
    payload: Any
    error: str | None = None

    @property
    def status(self) -> str:
        if not self.ok:
            return "failed"
        if self.item_count == 0:
            return "empty"
        return "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 2),
            "item_count": self.item_count,
            "error": self.error,
        }


class DisasterIntelPipeline:
    """
    Orchestrates the full data ingestion pipeline:
    1. Fetch data from all sources concurrently
    2. Normalize into unified models
    3. Run cross-source verification
    4. Detect correlated anomalies
    5. Persist raw and processed intelligence artifacts
    """

    def __init__(self):
        self.open_meteo = OpenMeteoIngestor()
        self.imd = IMDIngestor()
        self.usgs = USGSIngestor()
        self.gdacs = GDACSIngestor()
        self.reddit = RedditIngestor()
        self.news = FirecrawlNewsIngestor()

        self.verifier = CrossSourceVerifier()
        self.anomaly_detector = TemporalAnomalyDetector(window_minutes=30)
        self.fusion_engine = IncidentFusionEngine(time_window_hours=6.0)
        self.intelligence_engine = IncidentIntelligenceEngine()

        self.data_dir = Path(config.data_dir)
        self.raw_dir = Path(config.raw_dir)
        self.processed_dir = Path(config.processed_dir)

    async def run(self) -> Dict:
        """Execute the full ingestion and verification flow."""
        start_time = datetime.now(timezone.utc)
        logger.info("=" * 60)
        logger.info("DISASTER INTEL PIPELINE - Starting ingestion run")
        logger.info("=" * 60)

        logger.info("PHASE 1: Fetching data from all sources")
        source_runs = await asyncio.gather(
            self._run_source("open_meteo", self.open_meteo.ingest),
            self._run_source("imd", self.imd.ingest),
            self._run_source("usgs", self.usgs.ingest),
            self._run_source("gdacs", self.gdacs.ingest),
            self._run_source("reddit", self.reddit.ingest),
            self._run_source("news", self.news.ingest),
        )

        source_lookup = {run.name: run for run in source_runs}

        weather_snapshots: List[WeatherSnapshot] = []
        weather_events: List[DisasterEvent] = []
        if isinstance(source_lookup["open_meteo"].payload, tuple):
            weather_snapshots, weather_events = source_lookup["open_meteo"].payload

        imd_snapshots: List[WeatherSnapshot] = []
        imd_events: List[DisasterEvent] = []
        if isinstance(source_lookup["imd"].payload, tuple):
            imd_snapshots, imd_events = source_lookup["imd"].payload

        usgs_events = self._ensure_list(source_lookup["usgs"].payload)
        gdacs_events = self._ensure_list(source_lookup["gdacs"].payload)
        reddit_posts = self._ensure_list(source_lookup["reddit"].payload)
        news_posts = self._ensure_list(source_lookup["news"].payload)

        all_weather: List[WeatherSnapshot] = weather_snapshots + imd_snapshots
        all_social: List[SocialPost] = reddit_posts + news_posts
        all_events: List[DisasterEvent] = (
            weather_events + imd_events + usgs_events + gdacs_events
        )

        logger.info("PHASE 1 RESULTS")
        logger.info("  Weather snapshots: %s", len(all_weather))
        logger.info("  Weather severe events: %s", len(weather_events + imd_events))
        logger.info("  USGS earthquakes: %s", len(usgs_events))
        logger.info("  GDACS alerts: %s", len(gdacs_events))
        logger.info("  Social/news posts: %s", len(all_social))
        logger.info("  Total events: %s", len(all_events))

        logger.info("PHASE 2: Cross-source verification")
        verified_events = self.verifier.verify_events(
            all_events, all_weather, all_social
        )

        logger.info("PHASE 3: Detecting correlated anomalies")
        correlated_alerts = self.anomaly_detector.detect_correlated_spikes(
            verified_events, all_weather, all_social
        )

        logger.info("PHASE 4: Fusing incidents")
        incidents = self.fusion_engine.fuse(verified_events)

        logger.info("PHASE 5: Enriching incidents")
        incidents = self.intelligence_engine.enrich(incidents)

        logger.info("PHASE 6: Generating intelligence output")
        output = self._build_output(
            events=verified_events,
            incidents=incidents,
            weather=all_weather,
            social=all_social,
            correlated_alerts=correlated_alerts,
            start_time=start_time,
            source_runs=source_runs,
        )

        self._save_output(output, source_runs)
        self._print_summary(output)
        return output

    async def _run_source(
        self,
        name: str,
        ingest_func: Callable[[], Awaitable[Any]],
    ) -> SourceRunResult:
        """Run one source ingestor and capture health metadata."""
        started = time.perf_counter()
        try:
            payload = await ingest_func()
            return SourceRunResult(
                name=name,
                ok=True,
                duration_seconds=time.perf_counter() - started,
                item_count=self._count_payload_items(payload),
                payload=payload,
            )
        except Exception as exc:
            logger.error("[%s] Ingestion FAILED: %s", name, exc)
            return SourceRunResult(
                name=name,
                ok=False,
                duration_seconds=time.perf_counter() - started,
                item_count=0,
                payload=[],
                error=str(exc),
            )

    def _count_payload_items(self, payload: Any) -> int:
        """Estimate the number of records yielded by a source."""
        if payload is None:
            return 0
        if isinstance(payload, tuple):
            return sum(self._count_payload_items(item) for item in payload)
        if isinstance(payload, list):
            return len(payload)
        return 1

    def _ensure_list(self, payload: Any) -> List[Any]:
        """Normalize non-list payloads into an empty list."""
        return payload if isinstance(payload, list) else []

    def _build_output(
        self,
        events: List[DisasterEvent],
        incidents: List[Dict],
        weather: List[WeatherSnapshot],
        social: List[SocialPost],
        correlated_alerts: List[Dict],
        start_time: datetime,
        source_runs: List[SourceRunResult],
    ) -> Dict:
        """Build the unified intelligence output."""
        end_time = datetime.now(timezone.utc)

        critical = [item for item in incidents if item["severity"] == "critical"]
        high = [item for item in incidents if item["severity"] == "high"]
        moderate = [item for item in incidents if item["severity"] == "moderate"]

        timeline = [
            {
                "timestamp": incident["latest_timestamp"],
                "type": "fused_incident",
                "source": ", ".join(incident["sources"]),
                "disaster_type": incident["disaster_type"],
                "severity": incident["severity"],
                "title": incident["title"],
                "location": incident["location"].get("name"),
                "confidence": incident["confidence_score"],
                "freshness": incident["freshness_score"],
                "corroborated_by": incident["sources"],
                "event_count": incident["event_count"],
                "source_count": incident["source_count"],
                "verification_status": incident["verification_status"],
                "priority_bucket": incident["priority_bucket"],
                "incident_summary": incident["incident_summary"],
                "ai_relevance_index": incident["ai_relevance_index"],
                "verification_strength": incident["verification_strength"],
                "impact_radius_km": incident["impact_radius_km"],
            }
            for incident in incidents
        ]
        timeline.sort(key=lambda item: item["timestamp"], reverse=True)

        severe_weather = [
            snapshot.to_dict() for snapshot in weather if snapshot.is_severe_weather()
        ]
        healthy_sources = [run.name for run in source_runs if run.status == "ok"]
        empty_sources = [run.name for run in source_runs if run.status == "empty"]
        failed_sources = [run.name for run in source_runs if run.status == "failed"]

        return {
            "metadata": {
                "pipeline_run": start_time.isoformat(),
                "completed": end_time.isoformat(),
                "duration_seconds": (end_time - start_time).total_seconds(),
                "sources_queried": [run.name for run in source_runs],
                "healthy_sources": healthy_sources,
                "empty_sources": empty_sources,
                "failed_sources": failed_sources,
            },
            "summary": {
                "total_events": len(events),
                "total_incidents": len(incidents),
                "critical_events": len(critical),
                "high_severity_events": len(high),
                "moderate_events": len(moderate),
                "weather_snapshots": len(weather),
                "social_posts_analyzed": len(social),
                "correlated_alerts": len(correlated_alerts),
                "avg_confidence_score": (
                    round(sum(event.confidence_score for event in events) / len(events), 1)
                    if events
                    else 0
                ),
                "verified_incidents": len(
                    [item for item in incidents if item["verification_status"] == "verified"]
                ),
                "avg_ai_relevance_index": (
                    round(sum(item["ai_relevance_index"] for item in incidents) / len(incidents), 1)
                    if incidents
                    else 0
                ),
            },
            "ingestion": {
                "sources": [run.to_dict() for run in source_runs],
                "success_rate": (
                    round(len(healthy_sources) / len(source_runs), 2)
                    if source_runs
                    else 0.0
                ),
            },
            "correlated_alerts": correlated_alerts,
            "incidents": incidents[:50],
            "priority_incidents": incidents[:10],
            "events": [event.to_dict() for event in events[:100]],
            "timeline": timeline[:100],
            "severe_weather": severe_weather,
            "social_intelligence": {
                "total_posts": len(social),
                "disaster_related": len(
                    [post for post in social if post.is_disaster_related]
                ),
                "top_posts": [
                    post.to_dict()
                    for post in sorted(
                        social,
                        key=lambda item: item.credibility_score,
                        reverse=True,
                    )[:20]
                ],
            },
        }

    def _serialize_payload(self, payload: Any) -> Any:
        """Convert source payloads to JSON-safe structures."""
        if payload is None:
            return None
        if isinstance(payload, tuple):
            return [self._serialize_payload(item) for item in payload]
        if isinstance(payload, list):
            return [self._serialize_payload(item) for item in payload]
        if isinstance(payload, dict):
            return payload
        if hasattr(payload, "to_dict"):
            return payload.to_dict()
        return payload

    def _save_output(self, output: Dict, source_runs: List[SourceRunResult]):
        """Save raw source outputs and processed intelligence output."""
        os.makedirs(self.raw_dir, exist_ok=True)
        os.makedirs(self.processed_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        for run in source_runs:
            raw_path = self.raw_dir / f"{timestamp}_{run.name}.json"
            with open(raw_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "source": run.name,
                        "ingestion": run.to_dict(),
                        "payload": self._serialize_payload(run.payload),
                    },
                    handle,
                    indent=2,
                    default=str,
                )
            logger.info("  Saved raw: %s", raw_path)

        output_path = self.processed_dir / f"intelligence_{timestamp}.json"
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2, default=str)
        logger.info("  Saved: %s", output_path)

        latest_path = self.processed_dir / "latest_intelligence.json"
        with open(latest_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2, default=str)
        logger.info("  Saved: %s", latest_path)

    def _print_summary(self, output: Dict):
        """Print a human-readable summary to the console."""
        summary = output["summary"]
        metadata = output["metadata"]
        ingestion = output["ingestion"]

        print("\n" + "=" * 60)
        print("DISASTER INTELLIGENCE SUMMARY")
        print("=" * 60)
        print(f"  Run time: {metadata['duration_seconds']:.1f}s")
        print(f"  Raw events: {summary['total_events']}")
        print(f"  Fused incidents: {summary['total_incidents']}")
        print(f"  Critical incidents: {summary['critical_events']}")
        print(f"  High incidents: {summary['high_severity_events']}")
        print(f"  Moderate incidents: {summary['moderate_events']}")
        print(f"  Avg confidence: {summary['avg_confidence_score']}%")
        print(f"  Verified incidents: {summary['verified_incidents']}")
        print(f"  Avg AI relevance: {summary['avg_ai_relevance_index']}")
        print(f"  Correlated alerts: {summary['correlated_alerts']}")
        print(f"  Social posts analyzed: {summary['social_posts_analyzed']}")
        print(
            f"  Source health: {len(metadata['healthy_sources'])}/"
            f"{len(metadata['sources_queried'])} healthy"
        )

        if metadata["empty_sources"]:
            print(f"  Empty sources: {', '.join(metadata['empty_sources'])}")
        if metadata["failed_sources"]:
            print(f"  Failed sources: {', '.join(metadata['failed_sources'])}")

        print("\nINGESTION STATUS:")
        for source in ingestion["sources"]:
            status = source["status"].upper()
            print(
                f"  [{status}] {source['name']} "
                f"({source['item_count']} items in {source['duration_seconds']:.1f}s)"
            )

        if output["correlated_alerts"]:
            print("\nCORRELATED EVENT ALERTS:")
            for alert in output["correlated_alerts"]:
                print(
                    f"  [{alert['severity'].upper()}] "
                    f"{alert['disaster_type']} in {alert['location']} "
                    f"- {alert['source_count']} sources, {alert['event_count']} events"
                )

        if output["timeline"]:
            print("\nLATEST EVENTS (Top 10):")
            for entry in output["timeline"][:10]:
                print(
                    f"  [{entry['severity'][:4].upper()}] {entry['title'][:65]}"
                    f" (conf: {entry['confidence']}%, status: {entry['verification_status']})"
                )

        print("=" * 60 + "\n")


async def main():
    pipeline = DisasterIntelPipeline()
    return await pipeline.run()


if __name__ == "__main__":
    asyncio.run(main())
