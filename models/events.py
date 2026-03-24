"""
DisasterIntel Platform — Unified Event Models
Every data source normalizes into these common models.
This is the 'single source of truth' schema.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
import hashlib
import json
import math
from datetime import timezone


class DisasterType(Enum):
    EARTHQUAKE = "earthquake"
    FLOOD = "flood"
    CYCLONE = "cyclone"
    WILDFIRE = "wildfire"
    VOLCANO = "volcano"
    DROUGHT = "drought"
    STORM = "storm"
    LANDSLIDE = "landslide"
    TSUNAMI = "tsunami"
    HEATWAVE = "heatwave"
    UNKNOWN = "unknown"


class Severity(Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class SourceType(Enum):
    WEATHER_API = "weather_api"
    SEISMIC_SENSOR = "seismic_sensor"
    GOVERNMENT_ALERT = "government_alert"
    SATELLITE = "satellite"
    NEWS = "news"
    SOCIAL_MEDIA = "social_media"
    GLOBAL_ALERT_SYSTEM = "global_alert_system"


@dataclass
class GeoLocation:
    """Standardized geographic coordinate."""
    latitude: float
    longitude: float
    location_name: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None

    def to_dict(self) -> Dict:
        """Serialize location to a dictionary."""
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "name": self.location_name,
            "region": self.region,
            "country": self.country,
        }

    def distance_to(self, other: "GeoLocation") -> float:
        """Haversine distance in km between two points."""
        R = 6371  # Earth radius in km
        lat1, lat2 = math.radians(self.latitude), math.radians(other.latitude)
        dlat = math.radians(other.latitude - self.latitude)
        dlon = math.radians(other.longitude - self.longitude)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class DisasterEvent:
    """
    Unified disaster event — the core data model.
    Every ingestion source produces instances of this.
    """
    event_id: str                          # Unique ID (source-specific hash)
    source: str                            # e.g., "usgs", "gdacs", "open_meteo"
    source_type: SourceType                # Category of source
    disaster_type: DisasterType            # What kind of disaster
    severity: Severity                     # Computed severity level

    # Spatiotemporal
    location: GeoLocation
    timestamp: datetime                    # When the event occurred / was reported
    ingested_at: datetime = field(default_factory=datetime.utcnow)

    # Core data
    title: str = ""
    description: str = ""
    raw_data: Dict = field(default_factory=dict)  # Original API response

    # Scoring (USP fields)
    confidence_score: float = 0.0          # 0-100: cross-source verification
    freshness_score: float = 1.0           # 0-1: decays over time
    relevance_score: float = 0.0           # 0-1: NLP relevance (for social/news)
    corroborating_sources: List[str] = field(default_factory=list)

    # Disaster-specific metadata
    magnitude: Optional[float] = None      # Earthquake magnitude
    depth_km: Optional[float] = None       # Earthquake depth
    precipitation_mm: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    affected_population: Optional[int] = None
    alert_level: Optional[str] = None      # GDACS: Green/Orange/Red
    impact_radius_km: Optional[float] = None

    # URLs & references
    source_url: Optional[str] = None
    image_urls: List[str] = field(default_factory=list)

    def compute_freshness(self, half_life_minutes: int = 60) -> float:
        """
        USP: Freshness decay scoring.
        Returns 0-1 where 1 = just happened, 0.5 = half_life minutes ago.
        Uses exponential decay: score = 2^(-age/half_life)
        """
        now = datetime.now(timezone.utc)
        ts = self.timestamp
        # Make both offset-aware for safe subtraction
        if ts.tzinfo is None:
            from datetime import timezone as tz
            ts = ts.replace(tzinfo=tz.utc)
        age_minutes = (now - ts).total_seconds() / 60
        if age_minutes <= 0:
            self.freshness_score = 1.0
            return self.freshness_score
        self.freshness_score = 2 ** (-age_minutes / half_life_minutes)
        return self.freshness_score

    def to_dict(self) -> Dict:
        """Serialize to dictionary for JSON storage."""
        return {
            "event_id": self.event_id,
            "source": self.source,
            "source_type": self.source_type.value,
            "disaster_type": self.disaster_type.value,
            "severity": self.severity.value,
            "location": {
                **self.location.to_dict(),
            },
            "timestamp": self.timestamp.isoformat(),
            "ingested_at": self.ingested_at.isoformat(),
            "title": self.title,
            "description": self.description,
            "confidence_score": self.confidence_score,
            "freshness_score": self.freshness_score,
            "relevance_score": self.relevance_score,
            "corroborating_sources": self.corroborating_sources,
            "magnitude": self.magnitude,
            "depth_km": self.depth_km,
            "precipitation_mm": self.precipitation_mm,
            "wind_speed_kmh": self.wind_speed_kmh,
            "affected_population": self.affected_population,
            "alert_level": self.alert_level,
            "impact_radius_km": self.impact_radius_km,
            "source_url": self.source_url,
            "image_urls": self.image_urls,
        }

    @staticmethod
    def generate_id(source: str, unique_key: str) -> str:
        """Generate deterministic event ID from source + key."""
        raw = f"{source}:{unique_key}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class WeatherSnapshot:
    """
    Hourly weather data for a monitored location.
    Used for cross-verification against social media claims.
    """
    location: GeoLocation
    timestamp: datetime
    source: str  # "open_meteo" or "imd"

    # Core weather params
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    precipitation_mm: Optional[float] = None
    rain_mm: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_gusts_kmh: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    weather_code: Optional[int] = None      # WMO code
    visibility_m: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    cape_jkg: Optional[float] = None        # Thunderstorm energy
    soil_moisture: Optional[float] = None
    snow_depth_m: Optional[float] = None

    def is_severe_weather(self) -> bool:
        """Quick check if this snapshot indicates severe conditions."""
        if self.precipitation_mm and self.precipitation_mm > 30:
            return True
        if self.wind_speed_kmh and self.wind_speed_kmh > 90:
            return True
        if self.weather_code and self.weather_code >= 95 and (
            (self.wind_speed_kmh or 0) > 40
            or (self.wind_gusts_kmh or 0) > 60
            or (self.precipitation_mm or 0) > 5
            or (self.cape_jkg or 0) > 2000
        ):
            return True
        if (
            self.cape_jkg
            and self.cape_jkg > 2200
            and (
                (self.precipitation_mm or 0) > 10
                or (self.wind_speed_kmh or 0) > 45
                or (self.weather_code or 0) >= 80
            )
        ):
            return True
        return False

    def get_severity(self) -> Severity:
        """Determine weather severity from parameters."""
        precip = self.precipitation_mm or 0
        wind = self.wind_speed_kmh or 0
        cape = self.cape_jkg or 0

        if precip > 60 or wind > 120 or (
            cape > 2600 and (precip > 10 or wind > 55 or (self.weather_code or 0) >= 95)
        ):
            return Severity.CRITICAL
        if precip > 30 or wind > 90 or (
            cape > 2000 and (precip > 5 or wind > 45 or (self.weather_code or 0) >= 95)
        ):
            return Severity.HIGH
        if precip > 15 or wind > 50 or (
            cape > 1200 and (precip > 2 or wind > 30 or (self.weather_code or 0) >= 80)
        ):
            return Severity.MODERATE
        return Severity.LOW

    def to_dict(self) -> Dict:
        return {
            "location": {
                "latitude": self.location.latitude,
                "longitude": self.location.longitude,
                "name": self.location.location_name,
            },
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "temperature_c": self.temperature_c,
            "humidity_pct": self.humidity_pct,
            "precipitation_mm": self.precipitation_mm,
            "rain_mm": self.rain_mm,
            "wind_speed_kmh": self.wind_speed_kmh,
            "wind_gusts_kmh": self.wind_gusts_kmh,
            "wind_direction_deg": self.wind_direction_deg,
            "weather_code": self.weather_code,
            "visibility_m": self.visibility_m,
            "cloud_cover_pct": self.cloud_cover_pct,
            "cape_jkg": self.cape_jkg,
            "soil_moisture": self.soil_moisture,
            "is_severe": self.is_severe_weather(),
            "severity": self.get_severity().value,
        }


@dataclass
class SocialPost:
    """
    Normalized social media / news post related to disasters.
    Used for NLP processing and cross-verification.
    """
    post_id: str
    source_platform: str        # "twitter", "reddit", "news"
    author: Optional[str] = None
    content: str = ""
    timestamp: Optional[datetime] = None
    location: Optional[GeoLocation] = None
    url: Optional[str] = None

    # NLP-computed fields
    disaster_type: Optional[DisasterType] = None
    is_disaster_related: bool = False
    relevance_score: float = 0.0            # 0-1 from NLP model
    sentiment: Optional[str] = None         # "negative", "neutral", "positive"
    extracted_locations: List[str] = field(default_factory=list)
    extracted_keywords: List[str] = field(default_factory=list)

    # Credibility (USP: based on the 16-feature credibility model)
    credibility_score: float = 0.0          # 0-1
    has_url: bool = False
    has_image: bool = False
    is_verified_author: bool = False

    def to_dict(self) -> Dict:
        return {
            "post_id": self.post_id,
            "source_platform": self.source_platform,
            "author": self.author,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "location": self.location.to_dict() if self.location and hasattr(self.location, 'to_dict') else None,
            "url": self.url,
            "disaster_type": self.disaster_type.value if self.disaster_type else None,
            "is_disaster_related": self.is_disaster_related,
            "relevance_score": self.relevance_score,
            "sentiment": self.sentiment,
            "credibility_score": self.credibility_score,
        }
