"""FastAPI app serving intelligence data and the GIS dashboard."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.services.intelligence import IntelligenceRepository
from backend.services.ingestion_runner import IngestionRunner
from backend.services.nlp_service import NlpService
from backend.services.verification import IncidentVerificationService


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "frontend"

app = FastAPI(title="DisasterIntel API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

repository = IntelligenceRepository()
nlp_service = NlpService()
verification_service = IncidentVerificationService()
ingestion_runner = IngestionRunner()


class ClassifyRequest(BaseModel):
    text: str


@app.get("/api/health")
def health() -> Dict:
    payload = repository.load_latest()
    return {
        "status": "ok",
        "latest_available": bool(payload.get("metadata")),
        "mapbox_token_configured": bool(os.getenv("MAPBOX_ACCESS_TOKEN")),
    }


@app.get("/api/intelligence/latest")
def latest_intelligence() -> Dict:
    return repository.load_latest()


@app.get("/api/events")
def list_events() -> Dict:
    events = repository.get_events()
    return {"items": events, "count": len(events)}


@app.get("/api/incidents")
def list_incidents() -> Dict:
    incidents = repository.get_incidents()
    return {"items": incidents, "count": len(incidents)}


@app.get("/api/incidents/priority")
def list_priority_incidents() -> Dict:
    incidents = repository.get_priority_incidents()
    return {"items": incidents, "count": len(incidents)}


@app.get("/api/timeline")
def timeline() -> Dict:
    items = repository.get_timeline()
    return {"items": items, "count": len(items)}


@app.get("/api/map/layers")
def map_layers() -> Dict:
    return repository.get_map_layers()


@app.post("/api/nlp/classify")
def classify_text(request: ClassifyRequest) -> Dict:
    return nlp_service.classify_text(request.text)


@app.get("/api/incidents/{incident_id}/verify")
def verify_incident(incident_id: str) -> Dict:
    incident = repository.get_incident_by_id(incident_id)
    if not incident:
        return {"error": "incident_not_found", "incident_id": incident_id}
    return verification_service.verify_incident(incident)


@app.get("/api/ingestion/status")
def ingestion_status() -> Dict:
    return ingestion_runner.status()


@app.post("/api/ingestion/run")
def run_ingestion() -> Dict:
    return ingestion_runner.trigger(str(BASE_DIR))


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
