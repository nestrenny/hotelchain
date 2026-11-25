"""Acceptance tests against the built lakehouse and the API (SRS 12 Stage 5, 16).

Run the pipeline first:  python -m hip.pipelines.run all
Then:                    python -m pytest -q
"""
from __future__ import annotations

import json
import shutil

import duckdb
import pytest
from fastapi.testclient import TestClient

from hip.settings import AS_OF, FORECAST_HORIZON, LAKE

pytestmark = pytest.mark.skipif(not LAKE.exists(), reason="lakehouse not built")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(LAKE), read_only=True)
    yield c
    c.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from hip.api import app as app_module, workflow
    monkeypatch.setattr(workflow, "APPDB", tmp_path / "app.db")     # isolate workflow state from the real audit trail
    return TestClient(app_module.app)


def one(con, sql):
    return con.execute(sql).fetchall()[0][0]


# ---------------------------------------------------------------- medallion layers and data quality
def test_all_layers_are_populated(con):
    layers = dict(con.execute("SELECT layer, count(*) FROM ops.catalog_tables GROUP BY 1").fetchall())
    assert layers["bronze"] >= 25 and layers["silver"] >= 25 and layers["gold"] >= 35


def test_bronze_keeps_source_metadata(con):
    cols = {r[0] for r in con.execute("DESCRIBE bronze.pms_reservations_staysuite").fetchall()}
    assert {"_source_system", "_source_file", "_batch_id", "_ingested_at", "_row_hash", "_business_vertical"} <= cols


def test_two_pms_vendors_are_harmonised(con):
    rows = con.execute("SELECT source_system, count(DISTINCT property_id) FROM silver.reservation GROUP BY 1").fetchall()
    assert dict(rows) == {"StaySuite PMS": 3, "InnKeeper PMS": 3}
    assert {r[0] for r in con.execute("SELECT DISTINCT room_type FROM silver.reservation").fetchall()} == {"DLX", "PRM", "STE", "VIL"}
    assert one(con, "SELECT count(*) - count(DISTINCT reservation_id) FROM silver.reservation") == 0   # CDC versions collapsed


def test_defective_rows_are_quarantined_not_loaded(con):
    assert one(con, "SELECT count(*) FROM ops.quarantine") > 0
    assert one(con, "SELECT count(*) FROM bronze.pms_reservations_staysuite WHERE ConfirmationNo IS NULL") == 0


def test_guest_identity_is_unified_and_pseudonymised(con):
    assert one(con, "SELECT avg((source_systems > 1)::INT) FROM gold.dim_guest") > 0.3
    cols = {r[0] for r in con.execute("DESCRIBE gold.dim_guest").fetchall()}
    assert not cols & {"email", "first_name", "last_name", "email_norm"}


def test_loyalty_tier_history_is_scd2(con):
    assert one(con, "SELECT count(*) FROM gold.dim_customer_history WHERE NOT is_current AND valid_to IS NOT NULL") > 0
    assert one(con, "SELECT max(n) FROM (SELECT count(*) FILTER (WHERE is_current) AS n FROM gold.dim_customer_history GROUP BY customer_id)") == 1


def test_no_unexpected_critical_data_quality_failures(con):
    failed = {r[0] for r in con.execute("SELECT rule_id FROM ops.dq_results WHERE run_at = (SELECT max(run_at) FROM ops.dq_results) AND status = 'Failed'").fetchall()}
    assert failed == {"DQ-REC-RES"}          # the one reconciliation break deliberately planted in the ERP extract


def test_reconciliation_finds_exactly_the_planted_break(con):
    breaks = con.execute("SELECT property_id, business_vertical FROM ops.reconciliation WHERE status = 'Break'").fetchall()
    assert breaks == [("P004", "Restaurants")]


def test_incremental_ingestion_is_idempotent(tmp_path):
    from hip.pipelines import bronze
    copy = tmp_path / "lake.duckdb"
    shutil.copy(LAKE, copy)
    c = duckdb.connect(str(copy))
    before = one(c, "SELECT count(*) FROM bronze.pos_transactions")
    bronze.run(c, "test-rerun", only="pos_restaurant_lines")
    row = c.execute("SELECT rows_loaded, status FROM ops.ingestion_log WHERE batch_id = 'test-rerun'").fetchall()[0]
    assert row == (0, "Succeeded") and one(c, "SELECT count(*) FROM bronze.pos_transactions") == before
    c.close()


# ---------------------------------------------------------------- governed KPI definitions
def test_occupancy_adr_revpar_follow_enterprise_definitions(con):
    sold, avail, rev = con.execute("SELECT sum(rooms_sold), sum(rooms_available), sum(room_revenue_usd) FROM gold.fact_room_occupancy WHERE is_actual AND property_id = 'P001'").fetchall()[0]
    nights = one(con, "SELECT count(*) FROM silver.room_night WHERE is_actual AND property_id = 'P001'")
    assert sold == nights
    m = con.execute("SELECT sum(rooms_sold) / sum(rooms_available), sum(room_revenue_usd) / sum(rooms_sold), sum(room_revenue_usd) / sum(rooms_available) FROM gold.mart_executive_daily WHERE property_id = 'P001'").fetchall()[0]
    assert m[0] == pytest.approx(sold / avail) and m[1] == pytest.approx(rev / sold) and m[2] == pytest.approx(rev / avail)
    assert 0.3 < m[0] < 1.0 and one(con, "SELECT count(*) FROM gold.fact_room_occupancy WHERE rooms_sold > rooms_available") == 0


def test_gop_is_ledger_revenue_less_operating_expenses(con):
    r = con.execute("SELECT sum(gop_usd), sum(gl_revenue_usd) - sum(opex_usd) FROM gold.mart_executive_daily").fetchall()[0]
    assert r[0] == pytest.approx(r[1])


def test_facts_keep_their_grain(con):
    assert one(con, "SELECT count(*) - count(DISTINCT (check_id, line_no)) FROM gold.fact_restaurant_transactions") == 0
    assert one(con, "SELECT count(*) - count(DISTINCT (property_id, room_type, business_date)) FROM gold.fact_room_occupancy") == 0


# ---------------------------------------------------------------- AI Control Tower
def test_forecasts_cover_the_horizon_with_ordered_intervals(con):
    n, bad = con.execute("SELECT count(DISTINCT business_date), count(*) FILTER (WHERE lower_80 > forecast_value + 1e-6 OR upper_80 < forecast_value - 1e-6) FROM gold.fact_forecasts WHERE forecast_type = 'forecast'").fetchall()[0]
    assert n == FORECAST_HORIZON and bad == 0
    assert one(con, "SELECT count(*) FROM gold.fact_forecasts WHERE metric = 'hotel_rooms_sold' AND forecast_type = 'forecast' AND forecast_value > capacity + 1e-6") == 0
    assert one(con, "SELECT count(*) FROM gold.fact_forecasts WHERE model_version IS NULL") == 0


def test_hotel_forecast_exists_per_property_and_room_category(con):
    assert one(con, "SELECT count(DISTINCT (property_id, entity_id)) FROM gold.fact_forecasts WHERE metric = 'hotel_rooms_sold'") == 6 * 5


def test_forecast_models_beat_the_naive_baseline(con):
    rows = con.execute("SELECT model_id, metric_value, baseline_value FROM ai.model_registry WHERE deployment_status = 'Deployed' AND model_type = 'Forecast'").fetchall()
    assert len(rows) == 10 and all(m <= b for _, m, b in rows)


def test_classifiers_are_better_than_chance(con):
    auc = dict(con.execute("SELECT model_id, metric_value FROM ai.model_registry WHERE deployment_status = 'Deployed' AND model_type = 'Classification'").fetchall())
    assert auc["hotel_cancellation"] > 0.65 and auc["guest_churn"] > 0.7


def test_every_recommendation_carries_the_required_fields(con):
    df = con.execute("SELECT * FROM gold.fact_ai_recommendations").df()
    assert len(df) >= 20 and df["recommendation_id"].is_unique
    for col in ["business_vertical", "property_id", "problem", "recommended_action", "supporting_data", "estimated_impact_usd", "confidence", "priority", "recommendation_date", "approval_status", "owner_role"]:
        assert df[col].notna().all(), col
    assert df["confidence"].between(0, 1).all() and (df["approval_status"] == "Pending approval").all()
    assert all(len(json.loads(s)) >= 3 for s in df["supporting_data"])
    scenarios = set(df["scenario"])
    assert {"Low property profitability", "High operating expenditure", "Low retail stock", "Low menu profitability", "High customer churn risk"} <= scenarios


def test_planted_anomalies_are_detected(con):
    titles = " | ".join(r[0] for r in con.execute("SELECT title FROM ai.alerts").fetchall())
    assert "Energy & Water" in titles and "Dubai Palm" in titles          # utilities spike
    assert "discount rate" in titles                                       # retail discount leakage
    assert "above benchmark at Phuket" in titles                           # supplier price increase


# ---------------------------------------------------------------- API, row-level security and workflow
H = lambda p: {"X-Persona": p}  # noqa: E731


def test_every_dashboard_renders_for_the_ceo(client):
    for name in ["executive", "hotels", "restaurants", "waterpark", "retail", "spa", "marketing", "finance"]:
        r = client.get(f"/api/dashboard/{name}", headers=H("ceo"))
        assert r.status_code == 200, name
        spec = r.json()
        assert spec["kpis"] and spec["panels"] and all(k["value"] is not None for k in spec["kpis"]), name


def test_row_level_security_limits_properties(client):
    spec = client.get("/api/dashboard/executive?properties=P001,P003", headers=H("gm_dxb")).json()
    assert spec["properties"] == ["P003"]
    table = next(p for p in spec["panels"] if p["title"] == "Property comparison")
    assert [r["property"] for r in table["rows"]] == ["Dubai Palm"]
    assert {p["property_id"] for p in client.get("/api/meta", headers=H("gm_dxb")).json()["properties"]} == {"P003"}


def test_vertical_access_is_enforced(client):
    assert client.get("/api/dashboard/finance", headers=H("rev_como")).status_code == 403
    recs = client.get("/api/tower/recommendations", headers=H("rev_como")).json()
    assert recs and {(r["property_id"], r["business_vertical"]) for r in recs} == {("P001", "Hotels")}
    assert client.get("/api/tower/forecast?metric=finance_opex&property_id=P001&entity_id=ALL", headers=H("rev_como")).status_code == 403
    assert client.get("/api/tower/forecast?metric=hotel_rooms_sold&property_id=P003&entity_id=ALL", headers=H("rev_como")).status_code == 403
    assert client.get("/api/meta", headers=H("nobody")).status_code == 401


def test_export_requires_permission_and_is_audited(client):
    assert client.get("/api/dashboard/hotels/export?panel=4", headers=H("rev_como")).status_code == 403
    r = client.get("/api/dashboard/hotels/export?panel=4", headers=H("ceo"))
    assert r.status_code == 200 and "Channel" in r.text
    assert client.get("/api/audit", headers=H("ceo")).json()[0]["action"] == "export"


def test_recommendation_approval_workflow(client):
    rid = client.get("/api/tower/recommendations", headers=H("ceo")).json()[0]["recommendation_id"]
    act = lambda persona, body: client.post(f"/api/tower/recommendations/{rid}/action", json=body, headers=H(persona))  # noqa: E731
    assert act("fin_analyst", {"action": "approve"}).status_code == 403            # read-only role
    assert act("ceo", {"action": "reject"}).status_code == 422                     # reason required
    assert act("ceo", {"action": "implement"}).status_code == 409                  # must be approved first
    assert act("ceo", {"action": "approve", "comment": "Go ahead"}).json()["approval_status"] == "Approved"
    done = act("ceo", {"action": "implement", "realised_impact_usd": 12345}).json()
    assert done["approval_status"] == "Implemented" and done["realised_impact_usd"] == 12345
    hist = client.get(f"/api/tower/recommendations/{rid}/history", headers=H("ceo")).json()
    assert [h["action"] for h in hist] == ["implement", "approve"] and "Elena Laurent" in hist[0]["persona"]


def test_alert_lifecycle(client):
    aid = next(a for a in client.get("/api/tower/alerts", headers=H("ceo")).json() if a["severity"] == "Critical")["alert_id"]
    act = lambda body: client.post(f"/api/tower/alerts/{aid}/action", json=body, headers=H("ceo"))  # noqa: E731
    assert act({"action": "acknowledge"}).json()["status"] == "Acknowledged"
    assert act({"action": "escalate"}).json()["escalated_to"]
    assert act({"action": "resolve"}).status_code == 422                            # resolution comment required
    res = act({"action": "resolve", "comment": "Root cause fixed"}).json()
    assert res["status"] == "Resolved" and res["resolution_comment"] == "Root cause fixed"


def test_forecast_workspace_payload(client):
    d = client.get("/api/tower/forecast?metric=hotel_rooms_sold&property_id=P003&entity_id=ALL&horizon=60", headers=H("ceo")).json()
    assert len(d["future"]["x"]) == 60 and len(d["backtest"]["x"]) == 90
    assert d["future"]["x"][0] > AS_OF.isoformat() and 0 < d["accuracy"]["wape"] < 0.5
    assert d["model"]["version"] and d["weekly_accuracy"]


# ---------------------------------------------------------------- engineering and energy
def has_energy(con):
    return one(con, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'ai' AND table_name = 'energy_findings'") > 0


def test_energy_models_beat_their_baselines(con):
    if not has_energy(con):
        pytest.skip("energy stage not run")
    m = {r[0]: (r[1], r[2]) for r in con.execute("SELECT model_name, metric_value, baseline_value FROM ai.energy_models").fetchall()}
    assert m["Energy baseline and forecast"][0] < m["Energy baseline and forecast"][1]          # lower error than same-weekday-last-week
    assert m["Device failure prediction"][0] > 0.75                                              # ROC AUC
    top, average = m["Failure prediction: precision of the top 5%"]
    assert top > 5 * average                                                                     # the highest-risk 5% fail far more often than average


def test_planted_energy_problems_are_found(con):
    if not has_energy(con):
        pytest.skip("energy stage not run")
    assert con.execute("SELECT property_id FROM ai.energy_anomalies WHERE status = 'Anomaly'").fetchall() == [("P003",)]       # chiller fault
    assert con.execute("SELECT property_id FROM ai.water_leak WHERE status <> 'Normal'").fetchall() == [("P005",)]            # water leak
    worst = con.execute("SELECT property_id FROM ai.vacant_room_waste ORDER BY setback_ratio DESC LIMIT 1").fetchall()[0][0]
    assert worst == "P004"                                                                                                     # vacant rooms not set back


def test_energy_forecast_and_device_scores_are_complete(con):
    if not has_energy(con):
        pytest.skip("energy stage not run")
    assert con.execute("SELECT count(DISTINCT property_id), count(DISTINCT reading_date), count(*) FILTER (WHERE lower_80 > kwh OR upper_80 < kwh) FROM ai.energy_forecast").fetchall()[0] == (6, 30, 0)
    n, scored, bad = con.execute("SELECT (SELECT count(*) FROM gold.dim_asset), count(*), count(*) FILTER (WHERE failure_probability NOT BETWEEN 0 AND 1) FROM ai.device_risk").fetchall()[0]
    assert scored == n and bad == 0


def test_contract_reminders_cover_lapsed_and_ending_contracts(con):
    if not has_energy(con):
        pytest.skip("energy stage not run")
    lapsed, soon, missing = con.execute("SELECT count(*) FILTER (WHERE days_left < 0), count(*) FILTER (WHERE days_left BETWEEN 0 AND 30), count(*) FILTER (WHERE action IS NULL OR action = '') FROM ai.contract_reminders").fetchall()[0]
    assert lapsed == 3 and soon >= 3 and missing == 0
    expected = one(con, f"SELECT count(*) FROM gold.dim_asset_contract WHERE end_date <= DATE '{AS_OF}' + INTERVAL 120 DAY")
    assert one(con, "SELECT count(*) FROM ai.contract_reminders") == expected


def test_energy_page_renders_and_respects_access(client, con):
    if not has_energy(con):
        pytest.skip("energy stage not run")
    spec = client.get("/api/dashboard/energy", headers=H("eng")).json()
    assert len(spec["kpis"]) == 12 and all(k["value"] is not None for k in spec["kpis"]) and spec["actions"]
    titles = {p["title"] for p in spec["panels"]}
    assert {"Devices most likely to fail in the next 30 days", "Contract reminders", "Water leak watch", "Devices due for replacement"} <= titles
    assert client.get("/api/dashboard/energy", headers=H("rev_como")).status_code == 403                 # no access to engineering
    assert client.get("/api/dashboard/finance", headers=H("eng")).status_code == 403                     # engineering role cannot see finance
    gm = client.get("/api/dashboard/energy", headers=H("gm_dxb")).json()
    assert gm["properties"] == ["P003"] and all("Dubai Palm" in a["title"] for a in gm["actions"])


# ----------------------------------------------------------------------------- CxO morning report
def test_guest_experience_data_is_loaded(con):
    n, linked = con.execute("SELECT count(*), count(guest_key) FROM gold.fact_guest_complaints").fetchall()[0]
    assert n > 1000 and linked == n                           # every complaint is tied to a stay and a guest
    assert con.execute("SELECT count(*) FILTER (WHERE overall_satisfaction NOT BETWEEN 1 AND 5) FROM gold.fact_guest_feedback").fetchone()[0] == 0
    recent = dict(con.execute("""SELECT property_id, count(*) FROM gold.fact_guest_complaints WHERE category = 'Wi-Fi and in-room technology'
                                 AND logged_date > DATE '2026-10-13' GROUP BY 1""").fetchall())
    assert max(recent, key=recent.get) == "P006"               # the planted Singapore pattern


def test_morning_report_answers_every_question_for_the_ceo(client):
    s = client.get("/api/dashboard/morning", headers=H("ceo")).json()
    items = s["panels"][0]["items"]
    assert [i["n"] for i in items] == list(range(1, 14))
    for i in items:
        assert i["verdict"] in ("good", "watch", "bad") and i["read"] and i["action"] and i["link"].startswith("#/")
        assert all(k["value"] is not None for k in i["kpis"]), i["question"]
    labels = {k["label"] for i in items for k in i["kpis"]}
    assert {"TRevPAR", "GSS", "Complaints per 100 stays", "ROAS", "CPOR", "Payroll cost %", "Utilities cost %", "Forecast GOP"} <= labels
    assert sum(s["counts"].values()) == 13
    guests = next(i for i in items if i["n"] == 9)
    assert "Singapore" in guests["action"]                    # the planted complaint rise is named


def test_morning_report_follows_role_access(client):
    fb = client.get("/api/dashboard/morning", headers=H("fb_hkt")).json()
    assert [i["n"] for i in fb["panels"][0]["items"]] == [5] and fb["properties"] == ["P005"]
    assert client.get("/api/dashboard/morning", headers=H("eng")).status_code == 403
    assert "morning" not in client.get("/api/meta", headers=H("eng")).json()["pages"]
    assert "morning" in client.get("/api/meta", headers=H("rev_como")).json()["pages"]


def test_page_shell_fingerprints_scripts_so_deployments_are_picked_up(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert 'src="js/app.js?v=' in r.text and 'href="css/app.css?v=' in r.text
    assert client.get("/js/app.js").headers["cache-control"] == "no-cache"
