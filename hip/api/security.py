"""Simulated authentication and row-level security (SRS 10.1).

The persona header stands in for a Microsoft Entra ID token. Every query the API runs is filtered
to the persona's properties, and every endpoint checks the persona's business verticals - the
browser never receives rows outside the persona's scope.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd
from fastapi import Header, HTTPException

from hip.settings import VERTICALS, load, properties

ALL_PROPS = [p["property_id"] for p in properties()]


class Scope:
    def __init__(self, persona: dict):
        self.persona = persona
        self.id = persona["id"]
        self.properties = ALL_PROPS if persona["properties"] == "*" else list(persona["properties"])
        self.verticals = VERTICALS if persona["verticals"] == "*" else list(persona["verticals"])
        self.enterprise = persona["properties"] == "*"
        self.can_approve, self.can_export, self.can_admin = persona["can_approve"], persona["can_export"], persona["can_admin"]

    def props(self, requested: str | None) -> list[str]:
        """Intersect the requested property filter with what the persona may see."""
        want = [p for p in (requested or "").split(",") if p]
        got = [p for p in want if p in self.properties] if want else self.properties
        return got or self.properties

    def need_vertical(self, vertical: str) -> None:
        if vertical not in self.verticals:
            raise HTTPException(403, f"{self.persona['title']} does not have access to {vertical}")

    def sees(self, property_id: str, vertical: str) -> bool:
        ok_prop = self.enterprise if property_id == "ALL" else property_id in self.properties
        return ok_prop and (vertical in self.verticals or vertical == "Enterprise")

    def public(self) -> dict:
        return {**self.persona, "properties": self.properties, "verticals": self.verticals, "enterprise": self.enterprise}


def get_scope(x_persona: str | None = Header(default=None)) -> Scope:
    personas = {p["id"]: p for p in load("personas.json")["personas"]}
    if x_persona and x_persona not in personas:
        raise HTTPException(401, "Unknown persona")
    return Scope(personas[x_persona or "ceo"])


def clean(o):
    """Make query results JSON-safe: NaN/NaT -> null, numpy scalars -> Python, dates -> ISO strings."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, np.ndarray, pd.Series, pd.Index)):
        return [clean(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return [clean(r) for r in o.to_dict("records")]
    if o is None or o is pd.NaT:
        return None
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()[:19] if (o.hour or o.minute) else o.date().isoformat()
    if isinstance(o, date):
        return o.isoformat()
    try:
        if pd.isna(o):
            return None
    except (TypeError, ValueError):
        pass
    return o if isinstance(o, (str, int)) else json.loads(json.dumps(o, default=str))
