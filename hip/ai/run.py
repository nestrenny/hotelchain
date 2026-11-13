"""AI Control Tower batch: train -> validate -> register -> score -> recommend -> alert (SRS 7.1)."""
from __future__ import annotations

import pandas as pd

from hip.ai import forecasting, models
from hip.ai.recommend import Engine
from hip.settings import MODELS

REGISTRY_DDL = """CREATE TABLE IF NOT EXISTS ai.model_registry (model_id VARCHAR, model_name VARCHAR, version VARCHAR, business_vertical VARCHAR, model_type VARCHAR,
    business_objective VARCHAR, algorithm VARCHAR, features VARCHAR, training_rows BIGINT, training_from DATE, training_to DATE, series_count INTEGER, metric_name VARCHAR,
    metric_value DOUBLE, baseline_name VARCHAR, baseline_value DOUBLE, forecast_error DOUBLE, drift_score DOUBLE, drift_status VARCHAR, data_quality_status VARCHAR,
    explainability VARCHAR, trained_at TIMESTAMP, last_inference_at TIMESTAMP, deployment_status VARCHAR, run_id VARCHAR, approved_by VARCHAR)"""
DQ_SCOPE = {"Hotels": "reservation|room|HOT", "Restaurants": "restaurant|RES", "Waterpark": "WAT", "Retail": "retail|inventory|RET", "Spa & Leisure": "spa|SPA",
            "Marketing": "guest", "Finance": "gl_|procurement|REC"}


def run(con, run_id: str) -> None:
    print("AI Control Tower: training, validation and scoring")
    MODELS.mkdir(parents=True, exist_ok=True)
    con.execute(REGISTRY_DDL)
    records = forecasting.run(con, run_id)
    records.append(models.cancellation(con))
    records += models.churn_and_value(con)
    records.append(models.campaign_conversion(con))
    records.append(models.queue_prediction(con))
    dq = con.execute("SELECT rule_id, dataset, status FROM ops.dq_results WHERE run_at = (SELECT max(run_at) FROM ops.dq_results)").df()
    prior = dict(con.execute("SELECT model_id, count(*) FROM ai.model_registry GROUP BY 1").fetchall())
    con.execute("UPDATE ai.model_registry SET deployment_status = 'Archived' WHERE deployment_status = 'Deployed'")
    versions, registry = {}, {}
    for r in records:
        r["version"] = f"1.{prior.get(r['model_id'], 0)}"
        r["drift_status"] = "Stable" if r["drift_score"] < 0.10 else "Watch" if r["drift_score"] < 0.25 else "Drift - retrain"
        scope = dq[(dq["rule_id"] + dq["dataset"]).str.contains(DQ_SCOPE[r["business_vertical"]], regex=True)] if len(dq) else dq
        r["data_quality_status"] = "Failed checks" if (scope["status"] == "Failed").any() else "Warnings" if (scope["status"] == "Warning").any() else "Passed"
        r.update(last_inference_at=r["trained_at"], deployment_status="Deployed", run_id=run_id, approved_by="Model Risk Committee (prototype auto-approval)")
        versions[r["model_id"]], registry[r["model_id"]] = r["version"], r
    cols = [c[0] for c in con.execute("DESCRIBE ai.model_registry").fetchall()]
    con.register("_reg", pd.DataFrame(records)[cols])
    con.execute("INSERT INTO ai.model_registry SELECT * FROM _reg")
    con.execute("ALTER TABLE gold.fact_forecasts ADD COLUMN model_version VARCHAR")
    con.execute("UPDATE gold.fact_forecasts f SET model_version = r.version FROM ai.model_registry r WHERE r.model_id = f.model_id AND r.deployment_status = 'Deployed'")
    print("AI Control Tower: recommendations and alerts")
    Engine(con, versions, registry).run()
