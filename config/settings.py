"""
DisasterIntel Platform — Central Configuration
All API endpoints, thresholds, and platform settings.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class OpenMeteoConfig:
    """Open-Meteo Weather API — Free, no key required."""
    base_url: str = "https://api.open-meteo.com/v1/forecast"
    hourly_params: str = (
        "precipitation,rain,showers,snowfall,snow_depth,"
        "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
        "weather_code,cape,soil_moisture_0_to_1cm,"
        "visibility,cloud_cover,temperature_2m,relative_humidity_2m"
    )
    daily_params: str = (
        "precipitation_sum,rain_sum,wind_speed_10m_max,"
        "wind_gusts_10m_max,weather_code,temperature_2m_max,"
        "temperature_2m_min"
    )
    timezone: str = "auto"
    forecast_days: int = 3


@dataclass
class IMDConfig:
    """India Meteorological Department API."""
    city_forecast_url: str = "https://city.imd.gov.in/api/cityweather_loc.php"
    current_weather_url: str = "https://mausam.imd.gov.in/api/current_wx_api.php"
    district_rainfall_url: str = "https://mausam.imd.gov.in/api/districtwise_rainfall_api.php"
    state_rainfall_url: str = "https://mausam.imd.gov.in/api/statewise_rainfall_api.php"
    nowcast_url: str = "https://mausam.imd.gov.in/api/nowcast_district_api.php"


@dataclass
class USGSConfig:
    """USGS Earthquake API — Free, GeoJSON feeds."""
    # Real-time GeoJSON feeds (updated every minute)
    feed_base_url: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"
    # Significant earthquakes in the past hour/day/week
    significant_hour: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.geojson"
    significant_day: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson"
    significant_week: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.geojson"
    # All M4.5+ earthquakes (globally relevant)
    m45_day: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
    m45_week: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
    # All M2.5+ earthquakes
    m25_day: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
    # Custom query endpoint
    query_url: str = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    min_magnitude: float = 4.0


@dataclass
class GDACSConfig:
    """Global Disaster Alert and Coordination System API."""
    base_url: str = "https://www.gdacs.org/gdacsapi"
    events_list: str = "https://www.gdacs.org/gdacsapi/api/Events/geteventlist/search"
    event_data: str = "https://www.gdacs.org/gdacsapi/api/Events/geteventdata"
    # RSS feed as a reliable fallback
    rss_feed: str = "https://www.gdacs.org/xml/rss.xml"
    # Event types: EQ (earthquake), TC (tropical cyclone), FL (flood),
    #              VO (volcano), WF (wildfire), DR (drought)
    event_types: List[str] = field(default_factory=lambda: ["EQ", "TC", "FL", "VO", "WF"])
    alert_levels: List[str] = field(default_factory=lambda: ["Green", "Orange", "Red"])


@dataclass
class FirecrawlConfig:
    """Firecrawl for extracting disaster data from news/social sources."""
    api_key: str = os.getenv("FIRECRAWL_API_KEY", "fc-c5327eb072c048e78417ff450c30f19a")
    base_url: str = "https://api.firecrawl.dev/v1"
    search_url: str = "https://api.firecrawl.dev/v2/search"
    extract_url: str = "https://api.firecrawl.dev/v2/extract"
    # News sources to monitor for disaster information
    news_sources: List[str] = field(default_factory=lambda: [
        "https://www.ndtv.com/topic/natural-disaster",
        "https://timesofindia.indiatimes.com/topic/natural-disaster",
        "https://www.aljazeera.com/tag/natural-disasters/",
        "https://reliefweb.int/disasters",
        "https://www.reddit.com/r/disaster/",
        "https://www.reddit.com/r/weather/",
    ])


@dataclass
class ScoringConfig:
    """Thresholds for cross-source verification and severity scoring."""
    # Freshness decay — half-life in minutes (info loses 50% weight after this)
    freshness_half_life_minutes: int = 60

    # Cross-source verification weights
    source_weights: Dict[str, float] = field(default_factory=lambda: {
        "usgs": 1.0,        # Government seismic data — highest trust
        "gdacs": 1.0,       # UN-backed global alerts — highest trust
        "imd": 0.95,        # Indian government weather — very high trust
        "open_meteo": 0.90, # Multi-model weather data — high trust
        "news_verified": 0.75,  # Established news outlets
        "reddit": 0.40,     # Social media — lower trust, needs corroboration
        "social_media": 0.30,   # General social posts
    })

    # Weather severity thresholds
    precipitation_heavy_mm_hr: float = 30.0    # Heavy rain threshold
    precipitation_extreme_mm_hr: float = 60.0  # Extreme rain
    wind_severe_kmh: float = 90.0              # Severe wind
    wind_extreme_kmh: float = 120.0            # Extreme/hurricane wind

    # Earthquake severity
    earthquake_moderate: float = 4.5
    earthquake_strong: float = 6.0
    earthquake_major: float = 7.0

    # Confidence score thresholds
    confidence_low: int = 30
    confidence_medium: int = 60
    confidence_high: int = 80


@dataclass
class PlatformConfig:
    """Master configuration."""
    open_meteo: OpenMeteoConfig = field(default_factory=OpenMeteoConfig)
    imd: IMDConfig = field(default_factory=IMDConfig)
    usgs: USGSConfig = field(default_factory=USGSConfig)
    gdacs: GDACSConfig = field(default_factory=GDACSConfig)
    firecrawl: FirecrawlConfig = field(default_factory=FirecrawlConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)

    # Default monitoring locations (Indian cities prone to disasters)
    watch_locations: List[Dict] = field(default_factory=lambda: [
        {"name": "Mumbai", "lat": 19.076, "lon": 72.8777, "imd_id": "43003"},
        {"name": "Chennai", "lat": 13.0827, "lon": 80.2707, "imd_id": "43279"},
        {"name": "Kolkata", "lat": 22.5726, "lon": 88.3639, "imd_id": "42809"},
        {"name": "Delhi", "lat": 28.6139, "lon": 77.2090, "imd_id": "42182"},
        {"name": "Nagpur", "lat": 21.1458, "lon": 79.0882, "imd_id": "42867"},
        {"name": "Guwahati", "lat": 26.1445, "lon": 91.7362, "imd_id": "42410"},
        {"name": "Bhubaneswar", "lat": 20.2961, "lon": 85.8245, "imd_id": "42971"},
        {"name": "Thiruvananthapuram", "lat": 8.5241, "lon": 76.9366, "imd_id": "43371"},
    ])

    # Data storage paths
    data_dir: str = "data"
    raw_dir: str = "data/raw"
    processed_dir: str = "data/processed"


# Singleton config instance
config = PlatformConfig()
