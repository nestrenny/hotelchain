"""AI Control Tower read models (SRS 7.4): overview, forecasting workspace, recommendations,
alerts, model monitoring - all filtered to the persona's properties and business verticals."""
from __future__ import annotations

import json

import pandas as pd
from fastapi import HTTPException

from hip.api import workflow
from hip.settings import AS_OF

A = f"DATE '{AS_OF}'"
SEV = {"Critical": 0, "Warning": 1, "Info": 2}
PRI = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def _in(vals) -> str:
    return "(" + ", ".join(repr(v) for v in vals) + ")"


def recommendations(con, scope, props: list[str] | None = None) -> list[dict]:
    df = con.execute("SELECT * FROM gold.fact_ai_recommendations ORDER BY priority_score DESC").df()
    st = workflow.states("rec_state")
    names = dict(con.execute("SELECT property_id, short_name FROM gold.dim_property").fetchall())
    out = []
    for r in df.to_dict("records"):
        if not scope.sees(r["property_id"], r["business_vertical"]) or (props and r["property_id"] != "ALL" and r["property_id"] not in props):
            continue
        s = st.get(r["recommendation_id"], {})
        r["supporting_data"] = json.loads(r["supporting_data"])
        r["forecast_link"] = json.loads(r["forecast_link"]) if isinstance(r["forecast_link"], str) else None
        r["model_id"] = r["model_id"] if isinstance(r["model_id"], str) else None
        r["property"] = names.get(r["property_id"], "Group")
        r.update(approval_status=s.get("status", "Pending approval"), owner=s.get("owner") or r["owner_role"], decided_by=s.get("decided_by"), decided_at=s.get("decided_at"),
                 decision_comment=s.get("comment"), realised_impact_usd=s.get("realised_impact_usd"))
        out.append(r)
    return out


def alerts(con, scope, props: list[str] | None = None) -> list[dict]:
    df = con.execute("SELECT * FROM ai.alerts").df()
    st = workflow.states("alert_state")
    names = dict(con.execute("SELECT property_id, short_name FROM gold.dim_property").fetchall())
    out = []
    for r in df.to_dict("records"):
        if not scope.sees(r["property_id"], r["business_vertical"]) or (props and r["property_id"] != "ALL" and r["property_id"] not in props):
            continue
        s = st.get(r["alert_id"], {})
        r["property"] = names.get(r["property_id"], "Group")
        r.update(status=s.get("status", "Open"), assignee=s.get("assignee") or r["owner_role"], escalated_to=s.get("escalated_to"), acknowledged_by=s.get("acknowledged_by"),
                 acknowledged_at=s.get("acknowledged_at"), resolved_by=s.get("resolved_by"), resolved_at=s.get("resolved_at"), resolution_comment=s.get("resolution_comment"))
        out.append(r)
    return sorted(out, key=lambda a: (a["status"] == "Resolved", SEV[a["severity"]], a["title"]))


def find(items: list[dict], key: str, value: str) -> dict:
    for i in items:
        if i[key] == value:
            return i
    raise HTTPException(404, "Not found, or outside your access scope")


def overview(con, scope, props: list[str]) -> dict:
    P = f"property_id IN {_in(props)}"
    fin = "Finance" in scope.verticals
    fc = con.execute(f"""SELECT metric, business_date AS d, sum(forecast_value) AS v, sum(lower_80) AS lo, sum(upper_80) AS hi FROM gold.fact_forecasts
                         WHERE forecast_type = 'forecast' AND metric IN ('finance_revenue', 'finance_gop') AND {P} GROUP BY ALL ORDER BY d""").df()
    hist = con.execute(f"SELECT business_date AS d, sum(gl_revenue_usd) AS rev, sum(gop_usd) AS gop FROM gold.mart_executive_daily WHERE {P} AND business_date > {A} - INTERVAL 60 DAY GROUP BY 1 ORDER BY 1").df()
    ly = con.execute(f"""SELECT sum(gl_revenue_usd) AS rev, sum(gop_usd) AS gop FROM gold.mart_executive_daily
                         WHERE {P} AND business_date > {A} - INTERVAL 364 DAY AND business_date <= {A} - INTERVAL 334 DAY""").fetchall()[0]
    r30 = fc[(fc["metric"] == "finance_revenue")].head(30)
    g30 = fc[(fc["metric"] == "finance_gop")].head(30)
    recs, al = recommendations(con, scope, props), alerts(con, scope, props)
    open_al = [a for a in al if a["status"] != "Resolved"]
    pend = [r for r in recs if r["approval_status"] == "Pending approval"]
    done = [r for r in recs if r["approval_status"] in ("Approved", "Implemented")]
    models = con.execute("SELECT drift_status, count(*) FROM ai.model_registry WHERE deployment_status = 'Deployed' GROUP BY 1").fetchall()
    risk = con.execute(f"""
        WITH f AS (SELECT property_id, sum(forecast_value) FILTER (WHERE metric = 'hotel_rooms_sold' AND entity_id = 'ALL') / sum(capacity) FILTER (WHERE metric = 'hotel_rooms_sold' AND entity_id = 'ALL') AS occ,
                          sum(forecast_value) FILTER (WHERE metric = 'finance_revenue') AS rev, sum(forecast_value) FILTER (WHERE metric = 'finance_gop') AS gop
                   FROM gold.fact_forecasts WHERE forecast_type = 'forecast' AND horizon_day <= 30 AND {P} GROUP BY 1),
             l AS (SELECT property_id, sum(rooms_sold) / sum(rooms_available) AS occ_ly, sum(gl_revenue_usd) AS rev_ly FROM gold.mart_executive_daily
                   WHERE {P} AND business_date > {A} - INTERVAL 364 DAY AND business_date <= {A} - INTERVAL 334 DAY GROUP BY 1)
        SELECT p.property_id, p.short_name AS property, f.occ AS forecast_occupancy, l.occ_ly AS occupancy_last_year, f.rev AS forecast_revenue, f.rev / l.rev_ly - 1 AS revenue_vs_last_year,
               f.gop / f.rev AS forecast_gop_margin
        FROM f JOIN l USING (property_id) JOIN gold.dim_property p USING (property_id) ORDER BY revenue_vs_last_year""").df()
    crit = pd.Series([a["property_id"] for a in open_al if a["severity"] == "Critical"]).value_counts() if open_al else pd.Series(dtype=int)
    risk["open_critical_alerts"] = risk["property_id"].map(crit).fillna(0).astype(int)
    risk["status"] = ["At risk" if (r["revenue_vs_last_year"] < -0.03 or r["forecast_gop_margin"] < 0.2 or r["open_critical_alerts"] > 0) else "Watch" if r["revenue_vs_last_year"] < 0.02 else "Healthy"
                      for _, r in risk.iterrows()]
    if not fin:
        risk = risk.drop(columns=["forecast_gop_margin"])
    by_v = {}
    for r in pend:
        by_v[r["business_vertical"]] = by_v.get(r["business_vertical"], 0) + r["estimated_impact_usd"]
    health = "At risk" if any(a["severity"] == "Critical" for a in open_al) and (risk["status"] == "At risk").mean() > 0.5 else "Watch" if any(a["severity"] == "Critical" for a in open_al) else "Healthy"
    return {
        "health": health,
        "tiles": [
            {"label": "Forecast revenue, next 30 days", "value": r30["v"].sum(), "fmt": "usd", "prev": ly[0], "prev_label": "same 30 days last year", "good": "up"},
            {"label": "Forecast GOP, next 30 days", "value": g30["v"].sum() if fin else None, "fmt": "usd", "prev": ly[1] if fin else None, "prev_label": "same 30 days last year", "good": "up"},
            {"label": "Forecast GOP margin", "value": (g30["v"].sum() / r30["v"].sum()) if fin and r30["v"].sum() else None, "fmt": "pct", "prev": (ly[1] / ly[0]) if fin and ly[0] else None, "prev_label": "last year", "good": "up"},
            {"label": "Critical exceptions open", "value": sum(a["severity"] == "Critical" for a in open_al), "fmt": "num", "good": "down"},
            {"label": "Recommendations awaiting approval", "value": len(pend), "fmt": "num", "good": "neutral"},
            {"label": "Opportunity awaiting approval", "value": sum(r["estimated_impact_usd"] for r in pend), "fmt": "usd", "good": "neutral"},
            {"label": "Approved or implemented impact", "value": sum(r["estimated_impact_usd"] for r in done), "fmt": "usd", "good": "up"},
            {"label": "Realised impact recorded", "value": sum((r["realised_impact_usd"] or 0) for r in recs), "fmt": "usd", "good": "up"},
        ],
        "revenue_chart": {
            "x": [d for d in list(hist["d"]) + list(fc[fc["metric"] == "finance_revenue"]["d"])],
            "actual": list(hist["rev"]) + [None] * FORECAST_LEN(fc), "forecast": [None] * len(hist) + list(fc[fc["metric"] == "finance_revenue"]["v"]),
            "lo": [None] * len(hist) + list(fc[fc["metric"] == "finance_revenue"]["lo"]), "hi": [None] * len(hist) + list(fc[fc["metric"] == "finance_revenue"]["hi"]),
        },
        "top_recommendations": pend[:6], "critical_alerts": [a for a in open_al if a["severity"] == "Critical"][:6],
        "opportunity_by_vertical": [{"vertical": k, "impact": v} for k, v in sorted(by_v.items(), key=lambda t: -t[1])],
        "alert_counts": {s: sum(a["severity"] == s for a in open_al) for s in SEV}, "risk_indicators": risk,
        "model_health": {k: v for k, v in models}, "as_of": AS_OF,
    }


def FORECAST_LEN(fc: pd.DataFrame) -> int:
    return int((fc["metric"] == "finance_revenue").sum())


def forecast_options(con, scope, props: list[str]) -> list[dict]:
    df = con.execute(f"""SELECT DISTINCT f.metric, r.model_name, f.business_vertical, f.property_id, p.short_name AS property, f.entity_id, f.entity_name, f.unit
                         FROM gold.fact_forecasts f JOIN ai.model_registry r ON r.model_id = f.model_id AND r.deployment_status = 'Deployed' JOIN gold.dim_property p USING (property_id)
                         WHERE f.property_id IN {_in(props)} ORDER BY f.business_vertical, f.metric, property, f.entity_name""").df()
    out = {}
    for r in df.to_dict("records"):
        if r["business_vertical"] not in scope.verticals:
            continue
        m = out.setdefault(r["metric"], {"metric": r["metric"], "name": r["model_name"], "vertical": r["business_vertical"], "unit": r["unit"], "series": []})
        m["series"].append({"property_id": r["property_id"], "property": r["property"], "entity_id": r["entity_id"], "entity_name": r["entity_name"]})
    return list(out.values())


def forecast(con, scope, metric: str, property_id: str, entity_id: str, horizon: int) -> dict:
    from hip.ai.families import FAMILIES

    if metric not in FAMILIES:
        raise HTTPException(404, "Unknown forecast")
    scope.need_vertical(FAMILIES[metric][1])
    if property_id not in scope.properties:
        raise HTTPException(403, "Property outside your access scope")
    horizon = min(max(horizon, 7), 90)
    f = con.execute("""SELECT business_date AS d, forecast_type, horizon_day, forecast_value AS v, lower_80 AS lo, upper_80 AS hi, actual_value AS actual, capacity, algorithm, model_version, entity_name, unit, generated_at
                       FROM gold.fact_forecasts WHERE metric = ? AND property_id = ? AND entity_id = ? ORDER BY business_date""", [metric, property_id, entity_id]).df()
    if not len(f):
        raise HTTPException(404, "No forecast for this selection")
    hist = con.execute(f"SELECT d, y FROM ({FAMILIES[metric][4]}) WHERE property_id = ? AND entity_id = ? AND d > {A} - INTERVAL 180 DAY ORDER BY d", [property_id, entity_id]).df()
    ly = con.execute(f"SELECT d + INTERVAL 364 DAY AS d, y FROM ({FAMILIES[metric][4]}) WHERE property_id = ? AND entity_id = ? AND d > {A} - INTERVAL 364 DAY AND d <= {A} - INTERVAL 364 DAY + INTERVAL {horizon} DAY ORDER BY d",
                     [property_id, entity_id]).df()
    acc = con.execute("SELECT * FROM ai.forecast_accuracy WHERE model_id = ? AND property_id = ? AND entity_id = ?", [metric, property_id, entity_id]).df().to_dict("records")[0]
    reg = con.execute("SELECT model_name, version, algorithm, features, trained_at, metric_name, drift_status, business_objective FROM ai.model_registry WHERE model_id = ? AND deployment_status = 'Deployed'", [metric]).df().to_dict("records")[0]
    bt, fut = f[f["forecast_type"] == "backtest"], f[(f["forecast_type"] == "forecast") & (f["horizon_day"] <= horizon)]
    b = bt.assign(week=pd.to_datetime(bt["d"]).dt.to_period("W-SUN").dt.start_time)
    weekly = b.groupby("week").apply(lambda g: pd.Series({"wape": (g["actual"] - g["v"]).abs().sum() / max(g["actual"].abs().sum(), 1e-9), "bias": (g["v"] - g["actual"]).sum() / max(g["actual"].abs().sum(), 1e-9)})).reset_index()
    related = [r for r in recommendations(con, scope) if r["forecast_link"] and r["forecast_link"]["metric"] == metric and r["forecast_link"]["property_id"] == property_id][:4]
    return {
        "metric": metric, "entity_name": f["entity_name"].iloc[0], "unit": f["unit"].iloc[0], "horizon": horizon, "model": reg, "champion": f["algorithm"].iloc[0], "generated_at": f["generated_at"].iloc[0],
        "history": {"x": list(hist["d"]), "y": list(hist["y"])},
        "backtest": {"x": list(bt["d"]), "forecast": list(bt["v"]), "actual": list(bt["actual"]), "lo": list(bt["lo"]), "hi": list(bt["hi"])},
        "future": {"x": list(fut["d"]), "forecast": list(fut["v"]), "lo": list(fut["lo"]), "hi": list(fut["hi"]), "capacity": list(fut["capacity"]), "last_year": list(ly["y"])[:len(fut)]},
        "accuracy": acc, "weekly_accuracy": weekly, "totals": {"forecast": fut["v"].sum(), "lo": fut["lo"].sum(), "hi": fut["hi"].sum(), "last_year": float(ly["y"].head(len(fut)).sum()) if len(ly) else None,
                                                               "capacity": fut["capacity"].sum() if fut["capacity"].notna().any() else None},
        "related_recommendations": related,
    }


def models(con, scope) -> dict:
    reg = con.execute("SELECT * FROM ai.model_registry ORDER BY trained_at DESC").df()
    reg = reg[reg["business_vertical"].isin(scope.verticals)]
    cur = reg[reg["deployment_status"] == "Deployed"].sort_values(["business_vertical", "model_name"])
    acc = con.execute(f"""SELECT a.model_id, p.short_name AS property, a.entity_name, a.champion, a.wape, a.wape_naive, a.bias, a.wape_h30, a.wape_h60, a.wape_h90, a.interval_coverage, a.drift_psi
                          FROM ai.forecast_accuracy a JOIN gold.dim_property p USING (property_id) WHERE a.property_id IN {_in(scope.properties)} ORDER BY a.model_id, a.wape DESC""").df()
    return {"deployed": cur, "history": reg[["model_id", "model_name", "version", "trained_at", "metric_value", "forecast_error", "drift_score", "deployment_status", "run_id"]], "series_accuracy": acc}
