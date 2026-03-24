"""
DisasterIntel — Social Media & News Ingestion
Uses Firecrawl for extracting disaster-related content from news sites,
Reddit, and other public sources. No Twitter/X API subscription needed.

Also supports direct Reddit JSON API (free, no key required).
"""

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

from ingestion.nlp import get_disaster_classifier
from models.events import (
    DisasterEvent, DisasterType, GeoLocation, Severity,
    SocialPost, SourceType,
)
from config.settings import config

logger = logging.getLogger(__name__)
classifier = get_disaster_classifier()

# Disaster keyword patterns for detection
DISASTER_KEYWORDS = {
    DisasterType.FLOOD: [
        r"\bflood(s|ing|ed)?\b", r"\bwaterlog(ging|ged)?\b",
        r"\bdeluge\b", r"\binundat(e|ion)\b", r"\bsubmerg(e|ed)\b",
        r"\bhigh water\b", r"\bflash flood\b",
    ],
    DisasterType.EARTHQUAKE: [
        r"\bearthquake\b", r"\bquake\b", r"\bseismic\b",
        r"\btremor\b", r"\brichter\b", r"\baftershock\b",
    ],
    DisasterType.CYCLONE: [
        r"\bcyclone\b", r"\bhurricane\b", r"\btyphoon\b",
        r"\bstorm surge\b", r"\btropical storm\b",
    ],
    DisasterType.WILDFIRE: [
        r"\bwildfire\b", r"\bforest fire\b", r"\bbushfire\b",
        r"\bfire(s)? spread\b", r"\bblaze\b",
    ],
    DisasterType.LANDSLIDE: [
        r"\blandslide\b", r"\bmudslide\b", r"\bdebris flow\b",
        r"\blandslip\b",
    ],
    DisasterType.TSUNAMI: [
        r"\btsunami\b", r"\btidal wave\b",
    ],
    DisasterType.STORM: [
        r"\bthunderstorm\b", r"\bhailstorm\b", r"\blightning\b",
        r"\btornado\b", r"\bsquall\b", r"\bblizzard\b",
    ],
    DisasterType.HEATWAVE: [
        r"\bheat ?wave\b", r"\bextreme heat\b", r"\bheat stroke\b",
    ],
}

# Indian city/location patterns for geolocation extraction
INDIA_LOCATION_PATTERNS = [
    r"\b(Mumbai|Delhi|Chennai|Kolkata|Bangalore|Bengaluru|Hyderabad)\b",
    r"\b(Nagpur|Pune|Ahmedabad|Jaipur|Lucknow|Bhopal|Patna)\b",
    r"\b(Guwahati|Bhubaneswar|Thiruvananthapuram|Kochi|Vizag)\b",
    r"\b(Kerala|Gujarat|Assam|Bihar|Odisha|Maharashtra|Tamil Nadu)\b",
    r"\b(Uttarakhand|Rajasthan|West Bengal|Andhra Pradesh|Karnataka)\b",
]


class RedditIngestor:
    """
    Fetches disaster-related posts from Reddit using public JSON API.
    No API key needed — Reddit serves JSON at any URL + .json
    """

    SUBREDDITS = [
        "IndianWeather",
        "india",       # r/india often has disaster reports
        "weather",
        "disasters",
        "tropicalweather",
    ]

    SEARCH_QUERIES = [
        "flood India",
        "earthquake India",
        "cyclone India",
        "disaster emergency",
        "flood rescue",
        "earthquake damage",
    ]

    async def fetch_subreddit(
        self, session: aiohttp.ClientSession, subreddit: str, limit: int = 25
    ) -> List[Dict]:
        """Fetch recent posts from a subreddit using public JSON API."""
        url = f"https://www.reddit.com/r/{subreddit}/new.json"
        params = {"limit": limit, "raw_json": 1}
        headers = {"User-Agent": "DisasterIntel/1.0 (Research Project)"}

        try:
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    posts = data.get("data", {}).get("children", [])
                    logger.info(
                        f"[Reddit] Fetched {len(posts)} posts from r/{subreddit}"
                    )
                    return [p.get("data", {}) for p in posts]
                elif resp.status == 429:
                    logger.warning(f"[Reddit] Rate limited on r/{subreddit}")
                    return []
                else:
                    logger.warning(f"[Reddit] HTTP {resp.status} for r/{subreddit}")
                    return []
        except Exception as e:
            logger.error(f"[Reddit] Error fetching r/{subreddit}: {e}")
            return []

    async def search_reddit(
        self, session: aiohttp.ClientSession, query: str, limit: int = 15
    ) -> List[Dict]:
        """Search Reddit for disaster-related posts."""
        url = "https://www.reddit.com/search.json"
        params = {
            "q": query,
            "sort": "new",
            "limit": limit,
            "t": "week",  # Last week only
            "raw_json": 1,
        }
        headers = {"User-Agent": "DisasterIntel/1.0 (Research Project)"}

        try:
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    posts = data.get("data", {}).get("children", [])
                    return [p.get("data", {}) for p in posts]
                return []
        except Exception as e:
            logger.error(f"[Reddit] Search error for '{query}': {e}")
            return []

    def parse_post(self, raw: Dict) -> Optional[SocialPost]:
        """Parse a Reddit post into a SocialPost."""
        title = raw.get("title", "")
        selftext = raw.get("selftext", "")
        content = f"{title}. {selftext}".strip()

        if not content or len(content) < 10:
            return None

        # Parse timestamp
        created_utc = raw.get("created_utc", 0)
        timestamp = datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else None

        post = SocialPost(
            post_id=raw.get("id", hashlib.md5(content.encode()).hexdigest()[:12]),
            source_platform="reddit",
            author=raw.get("author"),
            content=content,
            timestamp=timestamp,
            url=f"https://reddit.com{raw.get('permalink', '')}",
            has_url=bool(raw.get("url")),
            has_image=raw.get("post_hint") == "image" or bool(raw.get("preview")),
        )

        # Run disaster classification
        classification = classifier.classify(content)
        post.disaster_type = classification.disaster_type
        post.is_disaster_related = classification.is_disaster
        post.relevance_score = classification.confidence
        post.sentiment = classification.sentiment
        post.extracted_locations = extract_locations(content)

        # Basic credibility scoring
        post.credibility_score = self._score_credibility(raw)

        return post

    def _score_credibility(self, raw: Dict) -> float:
        """
        USP: Credibility scoring based on Reddit post metadata.
        Based on the research paper's credibility features adapted for Reddit.
        """
        score = 0.3  # Base score for any public post

        # Account age/karma signals
        upvote_ratio = raw.get("upvote_ratio", 0.5)
        score_val = raw.get("score", 0)
        num_comments = raw.get("num_comments", 0)

        # High upvote ratio = community endorsement
        if upvote_ratio > 0.8:
            score += 0.15
        elif upvote_ratio > 0.6:
            score += 0.05

        # Engagement signals
        if num_comments > 10:
            score += 0.1
        if score_val > 50:
            score += 0.1

        # Contains URL (external source reference)
        if raw.get("url") and not raw.get("is_self"):
            score += 0.1

        # Has image/media (verifiable)
        if raw.get("post_hint") == "image" or raw.get("preview"):
            score += 0.1

        # From a verified/known subreddit
        subreddit = raw.get("subreddit", "").lower()
        trusted_subs = {"indiaweather", "tropicalweather", "weather", "india"}
        if subreddit in trusted_subs:
            score += 0.1

        return min(score, 1.0)

    async def ingest(self) -> List[SocialPost]:
        """Fetch and parse all Reddit disaster data."""
        logger.info("[Reddit] Starting social media ingestion...")
        all_posts = []
        seen_ids = set()

        async with aiohttp.ClientSession() as session:
            # Fetch from subreddits
            sub_tasks = [
                self.fetch_subreddit(session, sub) for sub in self.SUBREDDITS
            ]
            sub_results = await asyncio.gather(*sub_tasks, return_exceptions=True)

            for result in sub_results:
                if isinstance(result, list):
                    for raw in result:
                        post = self.parse_post(raw)
                        if post and post.post_id not in seen_ids:
                            all_posts.append(post)
                            seen_ids.add(post.post_id)

            # Rate limit courtesy — small delay between subreddit and search
            await asyncio.sleep(1)

            # Search for disaster-specific content
            search_tasks = [
                self.search_reddit(session, q) for q in self.SEARCH_QUERIES
            ]
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

            for result in search_results:
                if isinstance(result, list):
                    for raw in result:
                        post = self.parse_post(raw)
                        if post and post.post_id not in seen_ids:
                            all_posts.append(post)
                            seen_ids.add(post.post_id)

        # Filter to disaster-related only
        disaster_posts = [p for p in all_posts if p.is_disaster_related]

        logger.info(
            f"[Reddit] Ingested {len(all_posts)} total posts, "
            f"{len(disaster_posts)} disaster-related"
        )
        return disaster_posts


class FirecrawlNewsIngestor:
    """
    Extracts disaster news from major news sites using Firecrawl.
    Falls back to direct HTML parsing if Firecrawl API key unavailable.
    """

    def __init__(self):
        self.api_key = config.firecrawl.api_key
        self.sources = config.firecrawl.news_sources

    async def extract_with_firecrawl(
        self, session: aiohttp.ClientSession, url: str
    ) -> Optional[Dict]:
        """Extract structured content from a URL using Firecrawl."""
        if not self.api_key:
            logger.warning("[Firecrawl] No API key configured, skipping")
            return None

        try:
            payload = {
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            async with session.post(
                f"{config.firecrawl.base_url}/scrape",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"[Firecrawl] Extracted content from {url}")
                    return data
                else:
                    logger.warning(f"[Firecrawl] HTTP {resp.status} for {url}")
                    return None
        except Exception as e:
            logger.error(f"[Firecrawl] Error extracting {url}: {e}")
            return None

    async def extract_reliefweb(
        self, session: aiohttp.ClientSession
    ) -> List[SocialPost]:
        """
        Fetch disaster reports from ReliefWeb API (UN OCHA).
        Free, structured, no key needed. Excellent news source.
        """
        url = "https://api.reliefweb.int/v1/disasters"
        params = {
            "appname": "disasterintel",
            "limit": 20,
            "sort[]": "date:desc",
            "fields[include][]": [
                "name", "description", "date.created", "country.name",
                "type.name", "status", "url",
            ],
        }

        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    posts = []
                    for item in data.get("data", []):
                        fields = item.get("fields", {})
                        post = SocialPost(
                            post_id=f"reliefweb_{item.get('id', '')}",
                            source_platform="reliefweb",
                            content=fields.get("name", ""),
                            timestamp=self._parse_date(
                                fields.get("date", {}).get("created")
                            ),
                            url=fields.get("url"),
                        )
                        classification = classifier.classify(post.content)
                        post.disaster_type = classification.disaster_type
                        post.is_disaster_related = True  # ReliefWeb is all disasters
                        post.relevance_score = classification.confidence
                        post.credibility_score = 0.9     # UN source
                        countries = fields.get("country", [])
                        if countries:
                            post.extracted_locations = [
                                c.get("name", "") for c in countries
                            ]
                        posts.append(post)

                    logger.info(f"[ReliefWeb] Fetched {len(posts)} disaster reports")
                    return posts
                return []
        except Exception as e:
            logger.error(f"[ReliefWeb] Error: {e}")
            return []

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    async def ingest(self) -> List[SocialPost]:
        """Fetch from all news sources."""
        logger.info("[News] Starting news ingestion...")
        all_posts = []

        async with aiohttp.ClientSession() as session:
            # Always fetch ReliefWeb (free, no key needed)
            reliefweb_posts = await self.extract_reliefweb(session)
            all_posts.extend(reliefweb_posts)

            # Firecrawl extraction if API key available
            if self.api_key:
                for source_url in self.sources[:3]:  # Limit to avoid rate limits
                    data = await self.extract_with_firecrawl(session, source_url)
                    if data:
                        # Parse extracted markdown into posts
                        content = data.get("data", {}).get("markdown", "")
                        if content:
                            post = SocialPost(
                                post_id=hashlib.md5(
                                    source_url.encode()
                                ).hexdigest()[:12],
                                source_platform="news",
                                content=content[:2000],  # Truncate
                                url=source_url,
                                timestamp=datetime.now(timezone.utc),
                            )
                            classification = classifier.classify(content)
                            post.disaster_type = classification.disaster_type
                            post.is_disaster_related = classification.is_disaster
                            post.relevance_score = classification.confidence
                            post.credibility_score = 0.75
                            all_posts.append(post)
                    await asyncio.sleep(1)  # Rate limit courtesy

        logger.info(f"[News] Ingested {len(all_posts)} news items")
        return all_posts


# ─── Shared NLP Utilities ────────────────────────────────────────────

def classify_disaster_type(text: str) -> Optional[DisasterType]:
    """
    Classify text into a disaster type using keyword regex matching.
    For MVP. In production, replace with DistilBERT/CrisisBERT model.
    """
    text_lower = text.lower()
    for dtype, patterns in DISASTER_KEYWORDS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return dtype
    return None


def extract_locations(text: str) -> List[str]:
    """
    Extract Indian locations from text using regex patterns.
    For MVP. In production, replace with SpaCy NER or fine-tuned LLM.
    """
    locations = []
    for pattern in INDIA_LOCATION_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        locations.extend(matches)
    return list(set(locations))


def compute_relevance_score(text: str) -> float:
    """
    Compute disaster relevance score for a piece of text.
    MVP: keyword density approach.
    Production: use sentence embeddings + cosine similarity.
    """
    text_lower = text.lower()
    words = text_lower.split()
    if not words:
        return 0.0

    disaster_word_count = 0
    all_patterns = []
    for patterns in DISASTER_KEYWORDS.values():
        all_patterns.extend(patterns)

    for pattern in all_patterns:
        disaster_word_count += len(re.findall(pattern, text_lower))

    # Relevance = ratio of disaster keywords to total words (capped at 1.0)
    return min(disaster_word_count / max(len(words), 1) * 5, 1.0)
