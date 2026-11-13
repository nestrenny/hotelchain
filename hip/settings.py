"""Central settings for the Hospitality Intelligence Platform (HIP) prototype."""
from __future__ import annotations

import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DATA = Path(os.environ.get("HIP_DATA", ROOT / "data"))
LANDING = DATA / "landing"          # stands in for the Azure ingestion landing zone
LAKE = DATA / "lakehouse.duckdb"    # stands in for the Fabric lakehouse (bronze/silver/gold/ops/ai schemas)
APPDB = DATA / "app.db"             # Control Tower workflow state + audit trail
MODELS = DATA / "models"            # model registry artefacts
WEB = ROOT / "web"
IMG = ROOT / "img"

# Last closed business date. Fixed so that the synthetic estate is reproducible.
AS_OF = date.fromisoformat(os.environ.get("HIP_AS_OF", "2026-11-12"))
HISTORY_START = date.fromisoformat(os.environ.get("HIP_HISTORY_START", "2024-11-01"))
FORECAST_HORIZON = 90
REPORTING_CCY = "USD"

VERTICALS = ["Hotels", "Restaurants", "Waterpark", "Retail", "Spa & Leisure", "Marketing", "Finance"]
REVENUE_VERTICALS = ["Hotels", "Restaurants", "Waterpark", "Retail", "Spa & Leisure"]


@lru_cache
def load(name: str):
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def properties() -> list[dict]:
    return load("properties.json")["properties"]
