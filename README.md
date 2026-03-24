# DisasterIntel

DisasterIntel is a multi-source disaster intelligence platform that ingests weather, seismic, global alert, social, and news signals, normalizes them into a common schema, verifies them across sources, fuses them into incidents, and serves a GIS dashboard for responders.

This repository currently implements:

- asynchronous multi-source ingestion
- unified disaster event models
- cross-source confidence scoring
- temporal anomaly detection
- incident fusion and prioritization
- FastAPI backend for data delivery and ingestion control
- Leaflet-based dashboard with priority incidents, map layers, and unified timeline
- incident-linked external verification workflow using Firecrawl search

## Architecture

The system is organized into four layers:

1. Ingestion
   - fetches raw data from Open-Meteo, USGS, GDACS, Reddit, Firecrawl/news, and IMD
   - source code lives under [`ingestion/sources`](/C:/Users/ACER/Disaster-intelligence/ingestion/sources)

2. Intelligence Processing
   - normalizes raw source data into common event objects
   - computes confidence and freshness
   - detects temporal anomalies
   - fuses raw events into incidents
   - enriches incidents with verification status, AI relevance index, impact radius estimate, and responder actions

3. Backend API
   - serves latest intelligence payloads and dashboard-ready layers
   - provides priority incident, verification, and ingestion control endpoints
   - backend code lives under [`backend`](/C:/Users/ACER/Disaster-intelligence/backend)

4. Frontend Dashboard
   - Leaflet GIS map
   - priority incident cards
   - unified event timeline
   - incident evidence/verification trail
   - frontend code lives under [`frontend`](/C:/Users/ACER/Disaster-intelligence/frontend)

## Data Flow

The main pipeline entry point is [`ingestion/pipeline.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/pipeline.py).

Pipeline phases:

1. Phase 1: Fetch source data in parallel
2. Phase 2: Cross-source verification
3. Phase 3: Temporal anomaly detection
4. Phase 4: Incident fusion
5. Phase 5: Incident intelligence enrichment
6. Phase 6: Persist raw and processed output

Processed outputs are written to:

- [`data/processed/latest_intelligence.json`](/C:/Users/ACER/Disaster-intelligence/data/processed/latest_intelligence.json)
- timestamped snapshots in [`data/processed`](/C:/Users/ACER/Disaster-intelligence/data/processed)

Raw per-source snapshots are written to:

- [`data/raw`](/C:/Users/ACER/Disaster-intelligence/data/raw)

## Core Modules

### Event Models

Unified domain models are defined in [`models/events.py`](/C:/Users/ACER/Disaster-intelligence/models/events.py).

Main objects:

- `DisasterEvent`
- `WeatherSnapshot`
- `SocialPost`
- `GeoLocation`

These models are the common schema used by all ingestion sources.

### Source Adapters

Implemented source adapters:

- [`open_meteo.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/sources/open_meteo.py)
- [`imd_weather.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/sources/imd_weather.py)
- [`usgs_earthquake.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/sources/usgs_earthquake.py)
- [`gdacs_alerts.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/sources/gdacs_alerts.py)
- [`social_news.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/sources/social_news.py)

### Verification

Cross-source verification logic lives in [`cross_verify.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/cross_verify.py).

It currently uses:

- source trust weighting
- time-window correlation
- simple location overlap
- social credibility heuristics

### Fusion

Incident fusion lives in [`fusion.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/fusion.py).

It merges repeated events into responder-facing incidents based on:

- disaster type
- location key
- time window

### Intelligence Enrichment

Incident intelligence enrichment lives in [`intelligence.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/intelligence.py).

It adds:

- `verification_status`
- `priority_bucket`
- `incident_summary`
- `recommended_actions`
- `ai_relevance_index`
- `verification_strength`
- `impact_radius_km`

### NLP

NLP utilities live in [`nlp.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/nlp.py).

Current behavior:

- tries transformer-based classification first
- falls back to rule-based classification when model loading is unavailable
- supports optional sentiment analysis

Important note:

- if PyTorch or a configured transformer model is not available in the running environment, NLP falls back to rule-based inference

## Backend API

Main API app: [`backend/app.py`](/C:/Users/ACER/Disaster-intelligence/backend/app.py)

Key endpoints:

- `GET /api/health`
- `GET /api/intelligence/latest`
- `GET /api/events`
- `GET /api/incidents`
- `GET /api/incidents/priority`
- `GET /api/timeline`
- `GET /api/map/layers`
- `GET /api/incidents/{incident_id}/verify`
- `GET /api/ingestion/status`
- `POST /api/ingestion/run`

### Incident Verification Workflow

Incident-linked external verification is implemented in [`verification.py`](/C:/Users/ACER/Disaster-intelligence/backend/services/verification.py).

Flow:

1. build a targeted search query from incident title, type, location, and supporting titles
2. call Firecrawl search
3. analyze returned evidence with classifier and sentiment pipeline
4. infer stance: `supporting`, `refuting`, `related`, or `unclear`
5. compute verification verdict:
   - `externally_corroborated`
   - `possible_misinformation`
   - `needs_review`
   - `insufficient_external_evidence`

## Frontend

Frontend files:

- [`index.html`](/C:/Users/ACER/Disaster-intelligence/frontend/index.html)
- [`app.js`](/C:/Users/ACER/Disaster-intelligence/frontend/app.js)
- [`styles.css`](/C:/Users/ACER/Disaster-intelligence/frontend/styles.css)

Dashboard sections:

- GIS map with layered disaster overlays
- intelligence signal cards
- priority incident cards
- incident evidence trail
- unified timeline
- ingestion refresh and rerun controls

Refresh behavior:

- ingestion status is polled periodically
- dashboard data reloads only on manual refresh or after a completed ingestion run
- the previous blind interval-based dashboard refresh was removed

## Configuration

Primary configuration lives in [`config/settings.py`](/C:/Users/ACER/Disaster-intelligence/config/settings.py).

Configured source families:

- Open-Meteo
- IMD
- USGS
- GDACS
- Firecrawl

Environment variables used by the codebase include:

- `FIRECRAWL_API_KEY`
- `CRISISBERT_MODEL_ID`
- `DISTILBERT_MODEL_ID`
- `DISASTER_TEXT_MODEL_ID`
- `DISASTER_TEXT_LABEL_MAPPING`
- `SENTIMENT_MODEL_ID`
- `MAPBOX_ACCESS_TOKEN`

## Installation

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Running

### Run the ingestion pipeline

```powershell
python -m ingestion.pipeline
```

### Run the backend and dashboard

```powershell
python -m uvicorn backend.app:app --reload
```

Open:

- [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### Trigger ingestion from the API

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/ingestion/run
```

### Check ingestion status

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/ingestion/status
```

## Current USP Mapping

The project currently maps to the planned USP stack as follows:

| USP | Status | Current Implementation |
| --- | --- | --- |
| Cross-Source Verification | Implemented | Heuristic weighting in [`cross_verify.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/cross_verify.py) |
| Temporal Anomaly Correlation | Implemented | Spike detection in [`cross_verify.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/cross_verify.py) |
| AI Relevance Index | Implemented | Incident-level relevance scoring in [`intelligence.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/intelligence.py) |
| Impact Radius Estimation | Partially Implemented | Earthquake/event estimates in [`events.py`](/C:/Users/ACER/Disaster-intelligence/models/events.py) and [`intelligence.py`](/C:/Users/ACER/Disaster-intelligence/ingestion/intelligence.py) |
| Freshness Decay Scoring | Implemented | Freshness calculation in [`events.py`](/C:/Users/ACER/Disaster-intelligence/models/events.py) |
| Unified Event Timeline | Implemented | Incident timeline in processed output and dashboard |

## Known Limitations

This repository is functional, but still has important limitations:

- IMD often returns `401`, so that source may be empty unless access is authorized
- global feeds can dominate the dashboard without stronger local relevance filtering
- incident fusion is still mostly location-name and time-window based
- verification is heuristic, not full misinformation detection
- transformer NLP may fall back if model dependencies are unavailable at runtime
- Firecrawl verification depends on external API availability and key configuration
- some feeds are high-volume and need stronger spatial clustering/ranking for operational use

## Recommended Next Steps

Recommended engineering next steps:

1. Add local relevance filtering and India-first ranking
2. Split dashboard views into `Local Priority` and `Global Feed`
3. Improve spatial clustering for nearby earthquake/global alerts
4. Add richer evidence visualization on the map
5. Move verification from heuristics toward stronger claim-verification models

## Repository Structure

```text
backend/      FastAPI application and service layer
config/       Source and platform configuration
data/         Raw and processed pipeline outputs
frontend/     Dashboard HTML/CSS/JS
ingestion/    Ingestion, verification, fusion, NLP, intelligence logic
models/       Shared disaster event schema
tests/        Reserved for tests
```
