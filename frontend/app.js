const map = L.map("map", { zoomControl: true }).setView([22.5, 79.5], 5);

const osmTiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
});
osmTiles.addTo(map);

const eventLayer = L.layerGroup().addTo(map);
const weatherLayer = L.layerGroup().addTo(map);
const correlatedLayer = L.layerGroup().addTo(map);

const layers = {
  events: eventLayer,
  weather: weatherLayer,
  correlated: correlatedLayer,
};

let lastFinishedAt = null;

document.querySelectorAll("[data-layer-toggle]").forEach((input) => {
  input.addEventListener("change", (event) => {
    const name = event.target.dataset.layerToggle;
    if (event.target.checked) {
      layers[name].addTo(map);
    } else {
      map.removeLayer(layers[name]);
    }
  });
});

function severityColor(severity) {
  switch ((severity || "").toLowerCase()) {
    case "critical":
      return "#9f2f2f";
    case "high":
      return "#bf7a1f";
    case "moderate":
      return "#26734d";
    default:
      return "#57706d";
  }
}

async function loadDashboard() {
  const [intelligenceRes, mapRes, timelineRes, priorityRes] = await Promise.all([
    fetch("/api/intelligence/latest"),
    fetch("/api/map/layers"),
    fetch("/api/timeline"),
    fetch("/api/incidents/priority"),
  ]);

  const intelligence = await intelligenceRes.json();
  const mapData = await mapRes.json();
  const timeline = await timelineRes.json();
  const priority = await priorityRes.json();

  renderSummary(intelligence.summary || {});
  renderUspMetrics(intelligence.summary || {}, priority.items || []);
  renderMap(mapData);
  renderTimeline(timeline.items || []);
  renderPriority(priority.items || []);
}

function renderSummary(summary) {
  const cards = [
    ["Incidents", summary.total_incidents || 0],
    ["Raw events", summary.total_events || 0],
    ["Critical", summary.critical_events || 0],
    ["Correlated", summary.correlated_alerts || 0],
  ];

  document.getElementById("summary-cards").innerHTML = cards.map(([label, value]) => `
    <article class="metric-card">
      <span class="metric-label">${label}</span>
      <strong class="metric-value">${value}</strong>
    </article>
  `).join("");
}

function renderUspMetrics(summary, priorityItems) {
  const top = priorityItems[0] || {};
  const cards = [
    {
      label: "Cross-source verification",
      value: summary.verified_incidents || 0,
      body: "Incidents already verified or strongly corroborated.",
    },
    {
      label: "Temporal anomalies",
      value: summary.correlated_alerts || 0,
      body: "Multi-signal spikes detected within the correlation window.",
    },
    {
      label: "AI relevance index",
      value: summary.avg_ai_relevance_index || 0,
      body: "Average urgency and decision relevance across fused incidents.",
    },
    {
      label: "Top impact radius",
      value: top.impact_radius_km ? `${top.impact_radius_km} km` : "n/a",
      body: "Largest estimated response footprint among current priorities.",
    },
  ];

  document.getElementById("usp-metrics").innerHTML = cards.map((card) => `
    <article class="usp-card">
      <span class="metric-label">${card.label}</span>
      <strong>${card.value}</strong>
      <p>${card.body}</p>
    </article>
  `).join("");
}

function renderMap(data) {
  eventLayer.clearLayers();
  weatherLayer.clearLayers();
  correlatedLayer.clearLayers();

  const bounds = [];

  (data.event_markers || []).forEach((marker) => {
    const loc = marker.location || {};
    if (!Number.isFinite(loc.latitude) || !Number.isFinite(loc.longitude)) {
      return;
    }

    const leafletMarker = L.circleMarker([loc.latitude, loc.longitude], {
      radius: 8,
      weight: 2,
      color: severityColor(marker.severity),
      fillColor: severityColor(marker.severity),
      fillOpacity: 0.8,
    }).bindPopup(`
      <strong>${marker.title || "Event"}</strong><br>
      ${marker.disaster_type || "unknown"}<br>
      Severity: ${marker.severity || "unknown"}<br>
      Confidence: ${marker.confidence_score || 0}<br>
      Signals: ${marker.event_count || 0} / Sources: ${marker.source_count || 0}
    `);

    leafletMarker.addTo(eventLayer);
    bounds.push([loc.latitude, loc.longitude]);
  });

  (data.weather_overlay || []).forEach((item) => {
    const loc = item.location || {};
    if (!Number.isFinite(loc.latitude) || !Number.isFinite(loc.longitude)) {
      return;
    }

    const circle = L.circle([loc.latitude, loc.longitude], {
      radius: 45000,
      color: "#1f6fb2",
      fillColor: "#4a9fe6",
      fillOpacity: 0.18,
      weight: 2,
    }).bindPopup(`
      <strong>${item.title}</strong><br>
      Severity: ${item.severity || "unknown"}<br>
      Rain: ${item.precipitation_mm ?? "n/a"} mm<br>
      Wind: ${item.wind_speed_kmh ?? "n/a"} km/h
    `);

    circle.addTo(weatherLayer);
    bounds.push([loc.latitude, loc.longitude]);
  });

  (data.correlated_overlay || []).forEach((item, index) => {
    const coords = item.location_coords || {};
    const lat = Number.isFinite(coords.latitude) ? coords.latitude : 20 + index;
    const lon = Number.isFinite(coords.longitude) ? coords.longitude : 78 + index;
    const marker = L.marker([lat, lon], {
      icon: L.divIcon({
        className: "correlated-icon",
        html: `<div style="background:#102316;color:#fff;border-radius:999px;padding:8px 10px;font-size:11px;">${item.disaster_type || "alert"}</div>`,
      }),
    }).bindPopup(`
      <strong>${item.location || "Correlated alert"}</strong><br>
      Type: ${item.disaster_type || "unknown"}<br>
      Severity: ${item.severity || "unknown"}<br>
      Sources: ${(item.sources || []).join(", ")}
    `);
    marker.addTo(correlatedLayer);
  });

  if (bounds.length > 0) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }
}

function renderTimeline(items) {
  const container = document.getElementById("timeline");
  if (!items.length) {
    container.innerHTML = `<div class="timeline-item">No events available yet. Run the ingestion pipeline or connect live sources.</div>`;
    return;
  }

  container.innerHTML = items.map((item) => `
    <article class="timeline-item">
      <div class="timeline-meta severity-${item.severity}">
        ${new Date(item.timestamp).toLocaleString()} - ${item.source}
      </div>
      <div class="timeline-title">${item.title}</div>
      <div class="timeline-summary">${item.incident_summary || ""}</div>
      <div class="timeline-tags">
        <span class="tag">${item.disaster_type}</span>
        <span class="tag">${item.severity}</span>
        <span class="tag">${item.verification_status || "unknown"}</span>
        <span class="tag">${item.priority_bucket || "medium"}</span>
        <span class="tag">AI relevance ${item.ai_relevance_index || "n/a"}</span>
        <span class="tag">impact ${item.impact_radius_km || "n/a"} km</span>
        <span class="tag">confidence ${item.confidence}</span>
      </div>
    </article>
  `).join("");
}

async function verifyIncident(incidentId, container) {
  container.innerHTML = `<div class="evidence-item">Checking linked coverage...</div>`;
  const response = await fetch(`/api/incidents/${incidentId}/verify`);
  const payload = await response.json();

  if (payload.error) {
    container.innerHTML = `<div class="evidence-item">Verification failed: ${payload.error}</div>`;
    return;
  }

  const verdict = payload.verdict || {};
  const evidence = payload.evidence || [];
  container.innerHTML = `
    <div class="evidence-item">
      <strong>Verdict:</strong> ${verdict.label || "unknown"}<br>
      Supporting: ${verdict.supporting_count || 0},
      Refuting: ${verdict.refuting_count || 0},
      Related: ${verdict.related_count || 0}
    </div>
    ${evidence.map((item) => `
      <div class="evidence-item">
        <strong>${item.title || "Untitled source"}</strong><br>
        <a class="evidence-link" href="${item.url || "#"}" target="_blank" rel="noreferrer">${item.url || "No URL"}</a><br>
        Stance: ${item.stance} | Sentiment: ${item.sentiment.label}<br>
        ${item.snippet || ""}
      </div>
    `).join("")}
  `;
}

function renderPriority(items) {
  const container = document.getElementById("priority-incidents");
  if (!items.length) {
    container.innerHTML = `<div class="priority-card">No priority incidents available yet.</div>`;
    return;
  }

  container.innerHTML = items.slice(0, 5).map((item) => `
    <article class="priority-card" data-incident-id="${item.incident_id}">
      <div class="priority-topline">
        <h3>${item.title}</h3>
        <span class="badge badge-${item.verification_status}">${item.verification_status}</span>
      </div>
      <div class="timeline-tags">
        <span class="tag">${item.priority_bucket}</span>
        <span class="tag">${item.severity}</span>
        <span class="tag">${item.source_count} sources</span>
        <span class="tag">${item.event_count} signals</span>
        <span class="tag">AI relevance ${item.ai_relevance_index || "n/a"}</span>
        <span class="tag">impact ${item.impact_radius_km || "n/a"} km</span>
        <span class="tag">${item.verification_strength || "weak"} verification</span>
      </div>
      <p>${item.incident_summary || ""}</p>
      <div class="priority-actions">${(item.recommended_actions || []).slice(0, 2).join(" ")}</div>
      <div class="timeline-tags" style="margin-top:12px;">
        <button type="button" class="verify-button">Verify Coverage</button>
      </div>
      <div class="evidence-block"></div>
    </article>
  `).join("");

  container.querySelectorAll(".priority-card").forEach((card) => {
    const button = card.querySelector(".verify-button");
    const evidenceBlock = card.querySelector(".evidence-block");
    button.addEventListener("click", () => verifyIncident(card.dataset.incidentId, evidenceBlock));
  });
}

async function updateIngestionStatus() {
  const response = await fetch("/api/ingestion/status");
  const payload = await response.json();
  const banner = document.getElementById("ingestion-status");
  if (payload.running) {
    banner.textContent = `Ingestion running since ${new Date(payload.last_started).toLocaleTimeString()}.`;
    return;
  }
  if (payload.last_finished) {
    banner.textContent = `Last ingestion finished at ${new Date(payload.last_finished).toLocaleTimeString()} with exit code ${payload.last_exit_code}.`;
    if (payload.last_finished !== lastFinishedAt) {
      lastFinishedAt = payload.last_finished;
      loadDashboard();
    }
    return;
  }
  banner.textContent = "Ingestion status idle.";
}

document.getElementById("refresh-button").addEventListener("click", () => {
  loadDashboard();
});

document.getElementById("rerun-button").addEventListener("click", async () => {
  const banner = document.getElementById("ingestion-status");
  banner.textContent = "Triggering ingestion run...";
  await fetch("/api/ingestion/run", { method: "POST" });
  updateIngestionStatus();
});

setInterval(updateIngestionStatus, 10000);

loadDashboard().catch((error) => {
  document.getElementById("timeline").innerHTML = `
    <div class="timeline-item">Failed to load dashboard data: ${error.message}</div>
  `;
});
