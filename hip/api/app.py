"""HTTP API and static web host for the Hospitality Intelligence Platform prototype.

    uvicorn hip.api.app:app --port 8000
"""
from __future__ import annotations

import csv
import io
from contextlib import contextmanager
from datetime import date, timedelta

import duckdb
from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from hip.api import tower, workflow
from hip.api.dashboards import DASHBOARDS, Ctx
from hip.api.security import Scope, clean, get_scope
from hip.settings import AS_OF, HISTORY_START, IMG, LAKE, WEB, load

app = FastAPI(title="Aurelian Collection - Hospitality Intelligence Platform", docs_url="/api/docs", openapi_url="/api/openapi.json")


@contextmanager
def lake():
    """Read-only lakehouse connection per request, so a pipeline run is never blocked for long."""
    if not LAKE.exists():
        raise HTTPException(503, "The lakehouse has not been built yet. Run: python -m hip.pipelines.run all")
    try:
        con = duckdb.connect(str(LAKE), read_only=True)
    except duckdb.Error as exc:
        raise HTTPException(503, f"The lakehouse is being refreshed. Try again shortly. ({str(exc)[:120]})")
    try:
        yield con
    finally:
        con.close()


def period(start: str | None, end: str | None) -> tuple[date, date]:
    try:
        e = min(date.fromisoformat(end), AS_OF) if end else AS_OF
        s = date.fromisoformat(start) if start else e - timedelta(days=29)
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD")
    s = max(s, HISTORY_START)
    if s > e:
        raise HTTPException(422, "Start date must not be after end date")
    return s, e


@app.get("/api/meta")
def meta(scope: Scope = Depends(get_scope)):
    cfg = load("properties.json")
    with lake() as con:
        props = con.execute("SELECT property_id, property_name, short_name, brand_name, region, country, city, currency, timezone, total_rooms, has_waterpark FROM gold.dim_property ORDER BY property_id").df()
        glossary = con.execute("SELECT * FROM ops.kpi_glossary").df()
        last = con.execute("SELECT max(finished_at) FROM ops.ingestion_log").fetchall()[0][0]
    pages = ["executive"] + [name for name, (_, v) in DASHBOARDS.items() if v and v in scope.verticals]
    return clean({"chain": cfg["chain"], "reporting_currency": cfg["reporting_currency"], "as_of": AS_OF, "history_start": HISTORY_START, "last_refresh": last,
                  "persona": scope.public(), "personas": [{k: p[k] for k in ("id", "name", "title", "scope")} for p in load("personas.json")["personas"]],
                  "properties": props[props["property_id"].isin(scope.properties)], "pages": pages, "glossary": glossary})


def build_dashboard(name: str, scope: Scope, properties: str | None, start: str | None, end: str | None, compare: str) -> dict:
    if name not in DASHBOARDS:
        raise HTTPException(404, "Unknown dashboard")
    builder, vertical = DASHBOARDS[name]
    if vertical:
        scope.need_vertical(vertical)
    s, e = period(start, end)
    with lake() as con:
        return builder(Ctx(con, scope, scope.props(properties), s, e, compare if compare in ("pp", "ly") else "pp"))


@app.get("/api/dashboard/{name}")
def dashboard(name: str, properties: str | None = None, start: str | None = None, end: str | None = None, compare: str = "pp", scope: Scope = Depends(get_scope)):
    return JSONResponse(clean(build_dashboard(name, scope, properties, start, end, compare)))


@app.get("/api/dashboard/{name}/export")
def export(name: str, panel: int, properties: str | None = None, start: str | None = None, end: str | None = None, compare: str = "pp", scope: Scope = Depends(get_scope)):
    """CSV export of one panel. Permission-gated and written to the audit trail (SRS 6.9)."""
    if not scope.can_export:
        raise HTTPException(403, f"{scope.persona['title']} does not have export permission")
    spec = clean(build_dashboard(name, scope, properties, start, end, compare))
    if panel == -1:
        cols, rows = ["KPI", "Value", "Comparison"], [[k["label"], k["value"], k["prev"]] for k in spec["kpis"]]
        title = "KPIs"
    else:
        if not 0 <= panel < len(spec["panels"]):
            raise HTTPException(404, "Unknown panel")
        p = spec["panels"][panel]
        title = p["title"]
        if p["type"] == "table":
            cols, rows = [c["label"] for c in p["columns"]], [[r.get(c["key"]) for c in p["columns"]] for r in p["rows"]]
        elif p["type"] == "line":
            cols, rows = ["Date"] + [s["name"] for s in p["series"]], [[x] + [s["data"][i] for s in p["series"]] for i, x in enumerate(p["x"])]
        elif p["type"] == "bar":
            cols, rows = ["Category"] + [s["name"] for s in p["series"]], [[x] + [s["data"][i] for s in p["series"]] for i, x in enumerate(p["categories"])]
        else:
            cols, rows = [""] + p["x"], [[y] + p["values"][i] for i, y in enumerate(p["y"])]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{spec['title']} - {title}", f"{spec['period']['start']} to {spec['period']['end']}", f"Properties: {' '.join(spec['properties'])}", f"Exported by {scope.persona['name']}"])
    w.writerow(cols)
    w.writerows(rows)
    workflow.log_event(scope, "export", f"{name}/{title}", "export", f"{len(rows)} rows, {spec['period']['start']} to {spec['period']['end']}")
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}_{title.lower().replace(" ", "_")[:40]}.csv"'})


# ----------------------------------------------------------------------------- AI Control Tower
@app.get("/api/tower/overview")
def tower_overview(properties: str | None = None, scope: Scope = Depends(get_scope)):
    with lake() as con:
        return JSONResponse(clean(tower.overview(con, scope, scope.props(properties))))


@app.get("/api/tower/forecast/options")
def tower_forecast_options(properties: str | None = None, scope: Scope = Depends(get_scope)):
    with lake() as con:
        return JSONResponse(clean(tower.forecast_options(con, scope, scope.props(properties))))


@app.get("/api/tower/forecast")
def tower_forecast(metric: str, property_id: str, entity_id: str, horizon: int = Query(30), scope: Scope = Depends(get_scope)):
    with lake() as con:
        return JSONResponse(clean(tower.forecast(con, scope, metric, property_id, entity_id, horizon)))


@app.get("/api/tower/recommendations")
def tower_recommendations(properties: str | None = None, scope: Scope = Depends(get_scope)):
    with lake() as con:
        return JSONResponse(clean(tower.recommendations(con, scope, scope.props(properties) if properties else None)))


@app.get("/api/tower/recommendations/{rec_id}/history")
def rec_history(rec_id: str, scope: Scope = Depends(get_scope)):
    with lake() as con:
        tower.find(tower.recommendations(con, scope), "recommendation_id", rec_id)
    return workflow.history(rec_id)


@app.post("/api/tower/recommendations/{rec_id}/action")
def rec_action(rec_id: str, body: dict = Body(...), scope: Scope = Depends(get_scope)):
    with lake() as con:
        rec = tower.find(tower.recommendations(con, scope), "recommendation_id", rec_id)
    realised = body.get("realised_impact_usd")
    workflow.act_recommendation(scope, rec, body.get("action", ""), body.get("comment"), body.get("owner"), float(realised) if realised not in (None, "") else None)
    with lake() as con:
        return JSONResponse(clean(tower.find(tower.recommendations(con, scope), "recommendation_id", rec_id)))


@app.get("/api/tower/alerts")
def tower_alerts(properties: str | None = None, scope: Scope = Depends(get_scope)):
    with lake() as con:
        return JSONResponse(clean(tower.alerts(con, scope, scope.props(properties) if properties else None)))


@app.get("/api/tower/alerts/{alert_id}/history")
def alert_history(alert_id: str, scope: Scope = Depends(get_scope)):
    with lake() as con:
        tower.find(tower.alerts(con, scope), "alert_id", alert_id)
    return workflow.history(alert_id)


@app.post("/api/tower/alerts/{alert_id}/action")
def alert_action(alert_id: str, body: dict = Body(...), scope: Scope = Depends(get_scope)):
    with lake() as con:
        alert = tower.find(tower.alerts(con, scope), "alert_id", alert_id)
    workflow.act_alert(scope, alert, body.get("action", ""), body.get("comment"), body.get("assignee"))
    with lake() as con:
        return JSONResponse(clean(tower.find(tower.alerts(con, scope), "alert_id", alert_id)))


@app.get("/api/tower/models")
def tower_models(scope: Scope = Depends(get_scope)):
    with lake() as con:
        return JSONResponse(clean(tower.models(con, scope)))


# ----------------------------------------------------------------------------- data platform and governance
@app.get("/api/platform")
def platform(scope: Scope = Depends(get_scope)):
    with lake() as con:
        q = lambda s: con.execute(s).df()  # noqa: E731
        src = {s["id"]: s for s in load("sources.json")["sources"]}
        ing = q("""SELECT * FROM ops.ingestion_log QUALIFY row_number() OVER (PARTITION BY source_id ORDER BY started_at DESC) = 1 ORDER BY domain, source_id""")
        ing["connector"] = ing["source_id"].map(lambda s: src.get(s, {}).get("connector"))
        ing["frequency_minutes"] = ing["source_id"].map(lambda s: src.get(s, {}).get("frequency_minutes"))
        ing["mode"] = ing["source_id"].map(lambda s: src.get(s, {}).get("mode"))
        out = {
            "ingestion": ing,
            "batches": q("SELECT batch_id, min(started_at) AS started_at, count(*) AS sources, sum(rows_loaded) AS rows_loaded, sum(rows_quarantined) AS rows_quarantined, count(*) FILTER (WHERE status = 'Failed') AS failed, "
                         "date_diff('millisecond', min(started_at), max(finished_at)) / 1000.0 AS seconds FROM ops.ingestion_log GROUP BY 1 ORDER BY 2 DESC LIMIT 10"),
            "layers": q("SELECT layer, count(*) AS tables, sum(row_count) AS row_count FROM ops.catalog_tables GROUP BY 1 ORDER BY CASE layer WHEN 'bronze' THEN 1 WHEN 'silver' THEN 2 WHEN 'gold' THEN 3 ELSE 4 END"),
            "dq": q("SELECT * FROM ops.dq_results WHERE run_at = (SELECT max(run_at) FROM ops.dq_results) ORDER BY CASE status WHEN 'Failed' THEN 0 WHEN 'Warning' THEN 1 WHEN 'Monitored' THEN 2 ELSE 3 END, rule_id"),
            "reconciliation": q("SELECT r.*, p.short_name AS property FROM ops.reconciliation r JOIN gold.dim_property p USING (property_id) WHERE r.status = 'Break' ORDER BY abs(r.difference_local) DESC LIMIT 20"),
            "lineage": q("SELECT target, source, layer FROM ops.lineage ORDER BY target, source"),
            "catalog": q("SELECT * FROM ops.catalog_tables ORDER BY CASE layer WHEN 'bronze' THEN 1 WHEN 'silver' THEN 2 WHEN 'gold' THEN 3 ELSE 4 END, table_name"),
            "classification": q("SELECT classification, count(*) AS columns, count(DISTINCT layer || table_name) AS tables FROM ops.catalog_columns GROUP BY 1 ORDER BY 2 DESC"),
            "sensitive_columns": q("SELECT layer, table_name, column_name, classification FROM ops.catalog_columns WHERE classification LIKE 'Personal%' OR classification = 'Pseudonymised' ORDER BY layer, table_name, column_name"),
            "glossary": q("SELECT * FROM ops.kpi_glossary"),
            "pipeline": q("SELECT layer, count(*) AS steps, sum(seconds) AS seconds, sum(row_count) AS row_count, count(*) FILTER (WHERE status = 'Failed') AS failed FROM ops.pipeline_runs "
                          "WHERE run_id = (SELECT max(run_id) FROM ops.pipeline_runs) GROUP BY 1"),
            "privacy": q("""SELECT count(*) AS guests, avg(is_member::INT) AS member_share, avg(marketing_consent::INT) FILTER (WHERE is_member) AS consent_rate, avg((source_systems > 1)::INT) AS multi_system_share,
                                   sum(source_records) AS source_records FROM gold.dim_guest""").to_dict("records")[0],
            "quarantine": q("SELECT source_id, reason, count(*) AS row_count, max(quarantined_at) AS last_seen FROM ops.quarantine GROUP BY 1, 2 ORDER BY 3 DESC"),
            "quarantine_sample": q("SELECT source_id, source_file, payload FROM ops.quarantine LIMIT 5") if scope.can_admin else None,
        }
    return JSONResponse(clean(out))


@app.get("/api/audit")
def audit(scope: Scope = Depends(get_scope)):
    rows = workflow.history(limit=300)
    return rows if (scope.can_admin or scope.enterprise) else [r for r in rows if r["persona_id"] == scope.id]


@app.get("/img/hero.jpg")
def hero():
    files = sorted(IMG.glob("*.jpg")) + sorted(IMG.glob("*.png"))
    if not files:
        raise HTTPException(404)
    return FileResponse(files[0], headers={"Cache-Control": "max-age=86400"})


app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
