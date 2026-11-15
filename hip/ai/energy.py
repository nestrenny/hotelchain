"""AI for engineering and energy: what the platform can predict about buildings and devices.

  1. Energy baseline and forecast    expected daily kWh from occupancy and weather; 30-day forecast
  2. Energy anomaly detection        days and end uses running above the expected baseline
  3. Vacant-room energy waste        how well empty rooms are set back, and what it costs
  4. Peak demand risk                predicted peak load against the contracted supply capacity
  5. Water leak detection            overnight flow against its normal baseline
  6. Device failure prediction       probability each device fails in the next 30 days
  7. Remaining useful life           estimated months of life left; repair-versus-replace
  8. Spares and workload forecast    expected failures, parts and technician hours by device type
  9. Contract and warranty reminders rule-based dates, enriched with the predicted failure exposure

Outputs are written to the `ai` schema and read by the Energy & Assets page.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score

from hip.settings import AS_OF, load

A = f"DATE '{AS_OF}'"
TA = pd.Timestamp(AS_OF)


def _save(con, name: str, df: pd.DataFrame) -> None:
    con.register("_tmp_df", df)
    con.execute(f"CREATE OR REPLACE TABLE ai.{name} AS SELECT * FROM _tmp_df")
    con.unregister("_tmp_df")


def _money(v: float) -> str:
    return f"${v / 1e6:,.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}K" if abs(v) >= 1e4 else f"${v:,.0f}"


def _n(count: int, noun: str) -> str:
    """'1 device' or '14 devices'."""
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def energy_models(con, cfg, names, findings, models) -> None:
    rates = pd.DataFrame([{"property_id": k, **{x: v[x] for x in ("tariff_usd_kwh", "water_usd_m3", "carbon_kg_kwh")}} for k, v in cfg["properties"].items()])
    _save(con, "energy_rates", rates)
    tariff = rates.set_index("property_id")
    d = con.execute("SELECT * FROM gold.mart_energy_daily ORDER BY property_id, reading_date").df()
    d["reading_date"] = pd.to_datetime(d["reading_date"])
    d["pcode"] = d["property_id"].astype("category").cat.codes
    d["vacant"], d["dow"], d["month"] = d["rooms_available"] - d["rooms_sold"], d["reading_date"].dt.day_of_week, d["reading_date"].dt.month
    cols = ["pcode", "rooms_sold", "vacant", "cooling_degrees", "heating_degrees", "dow", "month"]
    cut = TA - pd.Timedelta(days=60)
    tr, te = d["reading_date"] <= cut, d["reading_date"] > cut
    mk = lambda: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.06, max_depth=6, categorical_features=[0], random_state=0)  # noqa: E731
    base = mk().fit(d.loc[tr, cols], d.loc[tr, "kwh"])
    d["expected_kwh"] = base.predict(d[cols])
    d["naive_kwh"] = d.groupby("property_id")["kwh"].shift(7)
    # judge accuracy on normal operation: the planted Dubai fault is exactly what the baseline is meant NOT to follow
    normal = te & ~((d["property_id"] == "P003") & (d["reading_date"] > TA - pd.Timedelta(days=24)))
    wape = lambda a, b: float((a - b).abs().sum() / a.abs().sum())  # noqa: E731
    w_model, w_naive = wape(d.loc[normal, "kwh"], d.loc[normal, "expected_kwh"]), wape(d.loc[normal, "kwh"], d.loc[normal, "naive_kwh"])
    d["deviation"] = d["kwh"] / d["expected_kwh"] - 1
    _save(con, "energy_baseline", d[["property_id", "reading_date", "kwh", "expected_kwh", "deviation", "avg_temp_c", "rooms_sold", "rooms_available", "peak_kw", "water_m3", "night_water_m3_per_hour"]]
          .assign(reading_date=lambda x: x["reading_date"].dt.date))
    models.append(("Energy baseline and forecast", "Expected daily energy use from occupancy, weather and calendar; forecasts the next 30 days using the hotel occupancy forecast and the weather forecast.",
                   "Gradient boosting regressor", "Error (WAPE), 60-day holdout", w_model, "Same weekday last week", w_naive, int(tr.sum())))

    # ---- anomalies: last 30 days above the expected baseline, with the end use that moved most
    last = d[d["reading_date"] > TA - pd.Timedelta(days=30)]
    eu = con.execute(f"""SELECT property_id, end_use, avg(kwh) FILTER (WHERE reading_date > {A} - INTERVAL 14 DAY) AS recent, avg(kwh) FILTER (WHERE reading_date <= {A} - INTERVAL 30 DAY) AS prior
                         FROM gold.fact_energy_daily WHERE reading_date > {A} - INTERVAL 120 DAY GROUP BY ALL""").df()
    eu["change"] = eu["recent"] / eu["prior"] - 1
    rows = []
    for pid, g in last.groupby("property_id"):
        hot = g[g["deviation"] > 0.08]
        excess = float((hot["kwh"] - hot["expected_kwh"]).sum())
        top = eu[eu["property_id"] == pid].sort_values("change", ascending=False).iloc[0]
        status = "Anomaly" if len(hot) >= 5 and excess > 0 else "Normal"
        rows.append(dict(property_id=pid, days_above_baseline=len(hot), excess_kwh=excess if status == "Anomaly" else 0.0, excess_cost_usd=(excess * tariff.loc[pid, "tariff_usd_kwh"]) if status == "Anomaly" else 0.0,
                         avg_deviation=float(g["deviation"].tail(14).mean()), driver_end_use=top["end_use"], driver_change=float(top["change"]), status=status,
                         first_day=hot["reading_date"].min().date() if status == "Anomaly" else None))
        if status == "Anomaly":
            findings.append(("Critical" if excess * tariff.loc[pid, "tariff_usd_kwh"] > 10000 else "High", "Energy anomaly", pid,
                             f"{names[pid]}: energy use {g['deviation'].tail(14).mean():+.0%} above expected; {top['end_use']} is up {top['change']:+.0%}",
                             f"{len(hot)} of the last 30 days ran more than 8% above the baseline for the occupancy and weather. Excess {excess:,.0f} kWh since {hot['reading_date'].min():%d %b}. Inspect the plant serving {top['end_use']}.",
                             excess * tariff.loc[pid, "tariff_usd_kwh"] * 12, None))
    _save(con, "energy_anomalies", pd.DataFrame(rows))

    # ---- forecast: next 30 days
    final = mk().fit(d[cols], d["kwh"])
    fut = con.execute(f"""SELECT f.property_id, f.business_date AS reading_date, f.forecast_value AS rooms_sold, f.capacity AS rooms_available, w.cooling_degrees, w.heating_degrees, w.avg_temp_c
                          FROM gold.fact_forecasts f JOIN gold.fact_weather_daily w ON w.property_id = f.property_id AND w.weather_date = f.business_date
                          WHERE f.metric = 'hotel_rooms_sold' AND f.entity_id = 'ALL' AND f.forecast_type = 'forecast' AND f.horizon_day <= 30""").df()
    fut["reading_date"] = pd.to_datetime(fut["reading_date"])
    fut["pcode"] = fut["property_id"].map(dict(zip(d["property_id"], d["pcode"])))
    fut["vacant"], fut["dow"], fut["month"] = fut["rooms_available"] - fut["rooms_sold"], fut["reading_date"].dt.day_of_week, fut["reading_date"].dt.month
    fut["kwh"] = final.predict(fut[cols])
    # carry forward how each property is running now: a fault that is still present will still be drawing power
    recent = d[d["reading_date"] > TA - pd.Timedelta(days=14)].assign(fit=lambda x: final.predict(x[cols]))
    level = (recent.groupby("property_id")["kwh"].sum() / recent.groupby("property_id")["fit"].sum()).clip(0.9, 1.35)
    fut["kwh"] *= fut["property_id"].map(level)
    q = d[normal].groupby("property_id")["deviation"].quantile([0.1, 0.9]).unstack()
    fut["lower_80"], fut["upper_80"] = fut["kwh"] * (1 + fut["property_id"].map(q[0.1]).clip(upper=-0.03)), fut["kwh"] * (1 + fut["property_id"].map(q[0.9]).clip(lower=0.03))
    fut["cost_usd"], fut["carbon_kg"] = fut["kwh"] * fut["property_id"].map(tariff["tariff_usd_kwh"]), fut["kwh"] * fut["property_id"].map(tariff["carbon_kg_kwh"])
    _save(con, "energy_forecast", fut[["property_id", "reading_date", "kwh", "lower_80", "upper_80", "cost_usd", "carbon_kg", "rooms_sold", "avg_temp_c"]].assign(reading_date=lambda x: x["reading_date"].dt.date))

    # ---- peak demand against contracted capacity
    cap = dict(con.execute("SELECT property_id, contracted_demand_kw FROM gold.dim_asset_contract WHERE contract_kind = 'Energy supply'").fetchall())
    rows = []
    for pid, g in d.groupby("property_id"):
        ratio = float((g["peak_kw"] / g["kwh"]).tail(30).median())
        f7 = fut[fut["property_id"] == pid].sort_values("reading_date").head(7)
        pk, hi = float((f7["kwh"] * ratio).max()), float((f7["upper_80"] * ratio).max())
        day = f7.loc[(f7["kwh"] * ratio).idxmax(), "reading_date"].date()
        status = "Over limit" if pk > cap[pid] else "At risk" if hi > cap[pid] else "Within limit"
        rows.append(dict(property_id=pid, contracted_kw=cap[pid], recent_peak_kw=float(g["peak_kw"].tail(30).max()), predicted_peak_kw=pk, predicted_peak_high_kw=hi, peak_day=day,
                         headroom=1 - pk / cap[pid], status=status))
        if status != "Within limit":
            findings.append(("High" if status == "Over limit" else "Medium", "Peak demand", pid, f"{names[pid]}: predicted peak load {pk:,.0f} kW against {cap[pid]:,.0f} kW contracted",
                             f"The highest load in the next 7 days is expected on {day:%d %b}. Shift laundry and pre-cool public areas outside 18:00 to 21:00 to stay under the contracted capacity and avoid demand charges.", None, day))
    _save(con, "peak_demand", pd.DataFrame(rows))

    # ---- vacant-room waste: energy per vacant room-night relative to an occupied one
    v = con.execute(f"""
        SELECT property_id, sum(kwh) FILTER (WHERE end_use = 'Guest room HVAC (vacant rooms)') AS vacant_kwh, sum(kwh) FILTER (WHERE end_use = 'Guest room HVAC (occupied rooms)') AS occupied_kwh
        FROM gold.fact_energy_daily WHERE reading_date > {A} - INTERVAL 90 DAY GROUP BY 1""").df()
    rn = con.execute(f"SELECT property_id, sum(rooms_sold) AS occupied_nights, sum(rooms_available - rooms_sold) AS vacant_nights FROM gold.mart_executive_daily WHERE business_date > {A} - INTERVAL 90 DAY GROUP BY 1").df()
    v = v.merge(rn, on="property_id")
    target = cfg["target_vacant_setback_ratio"]
    v["setback_ratio"] = (v["vacant_kwh"] / v["vacant_nights"]) / (v["occupied_kwh"] / v["occupied_nights"])
    v["target_ratio"] = target
    v["annual_saving_kwh"] = np.where(v["setback_ratio"] > target + 0.03, v["vacant_kwh"] * (1 - target / v["setback_ratio"]) * 365 / 90, 0.0)
    v["annual_saving_usd"] = v["annual_saving_kwh"] * v["property_id"].map(tariff["tariff_usd_kwh"])
    _save(con, "vacant_room_waste", v)
    for _, r in v[v["annual_saving_usd"] > 5000].iterrows():
        findings.append(("High" if r["annual_saving_usd"] > 40000 else "Medium", "Energy saving", r["property_id"],
                         f"{names[r['property_id']]}: vacant rooms use {r['setback_ratio']:.0%} of the energy of an occupied room",
                         f"Best practice is about {target:.0%}. Link the room controllers to check-out and reservations so empty rooms are set back automatically. Estimated saving {r['annual_saving_kwh']:,.0f} kWh a year.",
                         r["annual_saving_usd"], None))

    # ---- water: overnight flow against its own baseline
    rows = []
    for pid, g in d.groupby("property_id"):
        basev = g[(g["reading_date"] <= TA - pd.Timedelta(days=30))].tail(90)["night_water_m3_per_hour"]
        med, mad = float(basev.median()), float((basev - basev.median()).abs().median()) or 0.01
        now = float(g["night_water_m3_per_hour"].tail(7).mean())
        z = (now - med) / (1.4826 * mad)
        leak = now > 1.5 * med and z > 6
        recent = g.tail(45)
        start = recent[recent["night_water_m3_per_hour"] > med + 6 * 1.4826 * mad]["reading_date"].min() if leak else None
        loss = (now - med) * 24 if leak else 0.0
        rows.append(dict(property_id=pid, baseline_night_m3_per_hour=med, current_night_m3_per_hour=now, z_score=float(z), status="Leak suspected" if leak else "Normal",
                         suspected_since=start.date() if leak else None, loss_m3_per_day=loss, annual_cost_usd=loss * 365 * tariff.loc[pid, "water_usd_m3"]))
        if leak:
            findings.append(("Critical", "Water leak", pid, f"{names[pid]}: probable water leak, about {loss:,.0f} m3 a day",
                             f"Overnight flow (02:00 to 05:00) has been {now:.1f} m3 an hour against a normal {med:.1f} since {start:%d %b}. Flow that does not fall at night points to a continuous leak. Isolate zones overnight to locate it.",
                             loss * 365 * tariff.loc[pid, "water_usd_m3"], None))
    _save(con, "water_leak", pd.DataFrame(rows))
    models.append(("Water leak detection", "Flags a property when overnight water flow rises well above its own 90-day baseline and stays there.", "Robust z-score (median and MAD) on overnight flow",
                   "Leaks flagged", float(sum(r["status"] != "Normal" for r in rows)), "Properties monitored", float(len(rows)), int(len(d))))
    models.append(("Energy anomaly detection", "Flags properties running more than 8% above the expected baseline on 5 or more of the last 30 days, and names the end use that moved most.",
                   "Deviation from the energy baseline model", "Properties flagged", float((pd.DataFrame(con.execute('SELECT status FROM ai.energy_anomalies').fetchall())[0] == 'Anomaly').sum()),
                   "Properties monitored", float(d["property_id"].nunique()), int(len(last))))


def device_models(con, cfg, names, findings, models) -> None:
    dt = {x["type"]: x for x in cfg["device_types"]}
    t = con.execute("SELECT t.*, a.install_date, a.expected_life_years, a.replacement_cost_usd, a.warranty_end, a.location, a.scope FROM gold.fact_device_telemetry t JOIN gold.dim_asset a USING (asset_id) ORDER BY asset_id, period_end").df()
    t["period_end"], t["install_date"] = pd.to_datetime(t["period_end"]), pd.to_datetime(t["install_date"])
    first = t["period_end"].min()
    t["k"] = ((t["period_end"] - first).dt.days // 30).astype(int)
    wo = con.execute("SELECT asset_id, completed_date, cost_usd, work_type FROM gold.fact_work_orders").df()
    wo["completed_date"] = pd.to_datetime(wo["completed_date"])
    wo["k"] = ((wo["completed_date"] - first).dt.days - 1) // 30
    cw = wo[wo["work_type"] == "Corrective"]
    t = t.merge(cw.groupby(["asset_id", "k"]).size().rename("failed").reset_index(), on=["asset_id", "k"], how="left")
    t = t.merge(wo.groupby(["asset_id", "k"])["cost_usd"].sum().rename("repair_cost").reset_index(), on=["asset_id", "k"], how="left")
    t[["failed", "repair_cost"]] = t[["failed", "repair_cost"]].fillna(0)
    t["failed"] = (t["failed"] > 0).astype(int)
    g = t.groupby("asset_id")
    t["age_years"] = (t["period_end"] - t["install_date"]).dt.days / 365
    t["age_ratio"] = t["age_years"] / t["expected_life_years"]
    t["faults_3"] = g["fault_count"].transform(lambda s: s.rolling(3, min_periods=1).sum())
    t["drift_change"] = t["power_drift_pct"] - g["power_drift_pct"].shift(3).fillna(t["power_drift_pct"])
    t["failures_prior_12"] = g["failed"].transform(lambda s: s.shift(1).rolling(12, min_periods=1).sum()).fillna(0)
    t["repair_cost_12"] = g["repair_cost"].transform(lambda s: s.shift(1).rolling(12, min_periods=1).sum()).fillna(0)
    t["type_code"], t["pcode"] = t["device_type"].astype("category").cat.codes, t["property_id"].astype("category").cat.codes
    cols = ["type_code", "pcode", "age_years", "age_ratio", "runtime_hours", "fault_count", "faults_3", "power_drift_pct", "drift_change", "days_since_service", "failures_prior_12"]
    last_k = int(t["k"].max())
    tr, te, now = t["k"] <= last_k - 4, t["k"].between(last_k - 3, last_k - 1), t["k"] == last_k
    model = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.07, max_depth=6, categorical_features=[0, 1], random_state=0).fit(t.loc[tr, cols], t.loc[tr, "failed"])
    p_te = model.predict_proba(t.loc[te, cols])[:, 1]
    auc = roc_auc_score(t.loc[te, "failed"], p_te)
    top = t.loc[te].assign(p=p_te).nlargest(int(te.sum() * 0.05), "p")
    lift, base_rate = float(top["failed"].mean()), float(t.loc[te, "failed"].mean())
    models.append(("Device failure prediction", "Probability that each device fails in the next 30 days, from its age, runtime, fault codes, power-draw drift and service history.",
                   "Gradient boosting classifier", "ROC AUC, 90-day holdout", float(auc), "Random ranking", 0.5, int(tr.sum())))
    models.append(("Failure prediction: precision of the top 5%", "Share of the 5% highest-risk devices that actually failed in the following 30 days, against the average failure rate.",
                   "Same model, ranked", "Failure rate in top 5%", lift, "Average failure rate", base_rate, int(te.sum())))

    s = t.loc[now].copy()
    s["failure_probability"] = model.predict_proba(s[cols])[:, 1]
    s["risk_band"] = np.where(s["failure_probability"] >= 0.40, "High", np.where(s["failure_probability"] >= 0.15, "Medium", "Low"))
    # devices typically run to about 1.35x their design life; measured efficiency loss shortens that
    s["remaining_life_months"] = np.maximum((s["expected_life_years"] * 1.35 - s["age_years"]) * 12 * (1 - np.minimum(s["power_drift_pct"] / 60, 0.7)), 0).round(0)
    s["under_warranty"] = pd.to_datetime(s["warranty_end"]) >= TA
    s["warranty_days_left"] = (pd.to_datetime(s["warranty_end"]) - TA).dt.days
    adr = dict(con.execute(f"SELECT property_id, sum(room_revenue_usd) / sum(rooms_sold) FROM gold.mart_executive_daily WHERE business_date > {A} - INTERVAL 90 DAY GROUP BY 1").fetchall())
    corr, prev, ooo = s["device_type"].map(lambda x: dt[x]["corrective_usd"]), s["device_type"].map(lambda x: dt[x]["preventive_usd"]), s["device_type"].map(lambda x: dt[x]["room_out_of_order"])
    s["cost_avoided_usd"] = s["failure_probability"] * ((corr - prev) + ooo * s["property_id"].map(adr))
    drivers = [np.where(s["faults_3"] >= 6, "rising fault codes", ""), np.where(s["power_drift_pct"] >= 15, "power draw " + s["power_drift_pct"].round(0).astype(int).astype(str) + "% above normal", ""),
               np.where(s["age_ratio"] >= 1, "past design life", ""), np.where(s["failures_prior_12"] >= 2, "repeat failures", ""), np.where(s["days_since_service"] > 330, "service overdue", "")]
    s["drivers"] = [", ".join(x for x in row if x) or "age and usage pattern" for row in zip(*drivers)]
    cover = con.execute(f"SELECT property_id, covers_device_types FROM gold.dim_asset_contract WHERE end_date >= {A} AND contract_kind <> 'Energy supply'").df()
    covered = {(r["property_id"], d.strip()) for _, r in cover.iterrows() for d in str(r["covers_device_types"]).split(",")}
    s["service_contract"] = [(p, d) in covered for p, d in zip(s["property_id"], s["device_type"])]
    s["replace_reason"] = np.where((s["repair_cost_12"] >= 0.5 * s["replacement_cost_usd"]) & (s["failures_prior_12"] >= 2), "Repeated repairs above 50% of replacement cost",
                                   np.where((s["age_ratio"] >= 1.0) & (s["failure_probability"] >= 0.15), "Past design life and likely to fail",
                                            np.where(s["remaining_life_months"] <= 6, "Under 6 months of life left", "")))
    s["action"] = np.where(s["replace_reason"] != "", "Plan replacement",
                           np.where((s["risk_band"] == "High") & s["under_warranty"], "Raise warranty claim and service now",
                                    np.where(s["risk_band"] == "High", "Service within 7 days", np.where(s["risk_band"] == "Medium", "Inspect at next room check", "Monitor"))))
    out = s[["asset_id", "property_id", "location", "device_type", "scope", "age_years", "expected_life_years", "age_ratio", "fault_count", "faults_3", "power_drift_pct", "days_since_service",
             "failures_prior_12", "repair_cost_12", "replacement_cost_usd", "failure_probability", "risk_band", "remaining_life_months", "under_warranty", "warranty_days_left", "warranty_end",
             "service_contract", "replace_reason", "action", "drivers", "cost_avoided_usd"]]
    _save(con, "device_risk", out)

    # ---- spares and technician workload for the next 30 days
    w = out.groupby(["property_id", "device_type"]).agg(devices=("asset_id", "size"), high_risk=("risk_band", lambda x: int((x == "High").sum())), expected_failures=("failure_probability", "sum")).reset_index()
    w["part"], w["technician_hours"] = w["device_type"].map(lambda x: dt[x]["part"]), w["expected_failures"] * w["device_type"].map(lambda x: dt[x]["tech_hours"])
    w["parts_to_stock"] = np.ceil(w["expected_failures"] * 1.2).astype(int)
    w["expected_repair_cost_usd"] = w["expected_failures"] * w["device_type"].map(lambda x: dt[x]["corrective_usd"])
    _save(con, "maintenance_workload", w)
    models.append(("Remaining useful life", "Months of life left for each device: design life less age, shortened by measured efficiency loss. Drives repair-versus-replace and the capital plan.",
                   "Engineering rule on telemetry (not a trained model)", "Devices flagged for replacement", float((out["replace_reason"] != "").sum()), "Devices assessed", float(len(out)), int(len(out))))
    models.append(("Spares and workload forecast", "Expected failures, parts to stock and technician hours for the next 30 days, by device type and property.",
                   "Sum of failure probabilities", "Expected failures, next 30 days", float(out["failure_probability"].sum()), "Actual failures, last 30 days",
                   float(t.loc[t["k"] == last_k - 1, "failed"].sum()), int(len(out))))

    for pid, gp in out[out["risk_band"] == "High"].groupby("property_id"):
        crit = gp[gp["scope"] == "plant"]
        findings.append(("Critical" if len(crit) else "High", "Predictive maintenance", pid, f"{names[pid]}: {_n(len(gp), 'device')} likely to fail within 30 days" + (f", including {crit.iloc[0]['location']}" if len(crit) else ""),
                         f"Most are: {', '.join(f'{t} ({k})' for t, k in gp['device_type'].value_counts().head(3).items())}. Servicing them before failure avoids an estimated {_money(gp['cost_avoided_usd'].sum())} of repair cost and lost room nights.",
                         float(gp["cost_avoided_usd"].sum()), None))

    # ---- replacement plan and capital forecast
    rp = out[out["replace_reason"] != ""].copy()
    rp["due_date"] = [TA + pd.DateOffset(months=int(min(m, 12))) for m in rp["remaining_life_months"]]
    rp["quarter"] = rp["due_date"].dt.to_period("Q").astype(str)
    _save(con, "replacement_plan", rp.groupby(["property_id", "device_type", "replace_reason"]).agg(devices=("asset_id", "size"), capex_usd=("replacement_cost_usd", "sum"), avg_age_years=("age_years", "mean"),
                                                                                                 avg_remaining_months=("remaining_life_months", "mean"), repair_cost_12=("repair_cost_12", "sum")).reset_index())
    _save(con, "capex_forecast", rp.groupby(["property_id", "quarter"]).agg(devices=("asset_id", "size"), capex_usd=("replacement_cost_usd", "sum")).reset_index())
    for pid, gp in rp.groupby("property_id"):
        topd = gp.groupby("device_type")["asset_id"].size().sort_values(ascending=False)
        findings.append(("High" if gp["replacement_cost_usd"].sum() > 250000 else "Medium", "Replacement due", pid, f"{names[pid]}: {_n(len(gp), 'device')} due for replacement, {_money(gp['replacement_cost_usd'].sum())} of capital",
                         f"The largest group is {topd.index[0]} ({topd.iloc[0]:,}). Reasons: past design life and likely to fail, under six months of life left, or repeated repairs above half the replacement cost. Include in the next capital plan.",
                         None, None))

    # ---- warranty reminders
    wr = out[(out["warranty_days_left"] >= 0) & (out["warranty_days_left"] <= 90)]
    wt = wr.groupby(["property_id", "device_type"]).agg(devices=("asset_id", "size"), first_expiry=("warranty_end", "min"), days_left=("warranty_days_left", "min"),
                                                        at_risk=("risk_band", lambda x: int((x != "Low").sum())), expected_failures=("failure_probability", "sum")).reset_index()
    wt["action"] = np.where(wt["at_risk"] > 0, "Inspect and claim before expiry", "Inspect before expiry")
    _save(con, "warranty_reminders", wt)
    for _, r in wt[wt["at_risk"] > 0].iterrows():
        findings.append(("Medium", "Warranty", r["property_id"], f"{names[r['property_id']]}: {r['devices']} {r['device_type'].lower()} warranties end within 90 days, {r['at_risk']} showing fault signs",
                         f"First expiry {pd.Timestamp(r['first_expiry']):%d %b %Y}. Inspect and raise claims while the manufacturer still bears the cost.", None, pd.Timestamp(r["first_expiry"]).date()))


def contract_reminders(con, names, findings) -> None:
    c = con.execute("SELECT * FROM gold.dim_asset_contract").df()
    risk = con.execute("SELECT property_id, device_type, count(*) AS devices, sum(failure_probability) AS expected_failures FROM ai.device_risk GROUP BY ALL").df()
    cost = {x["type"]: x["corrective_usd"] for x in load("energy.json")["device_types"]}
    rows = []
    for _, r in c.iterrows():
        end = pd.Timestamp(r["end_date"])
        days = (end - TA).days
        if days > 120:
            continue
        types = [d.strip() for d in str(r["covers_device_types"]).split(",") if d.strip()]
        rk = risk[(risk["property_id"] == r["property_id"]) & (risk["device_type"].isin(types))]
        exposure = float(sum(e * cost[d] for d, e in zip(rk["device_type"], rk["expected_failures"])))
        notice = end - pd.Timedelta(days=int(r["notice_days"]))
        status = "Expired" if days < 0 else "Ends within 30 days" if days <= 30 else "Ends within 60 days" if days <= 60 else "Ends within 90 days" if days <= 90 else "Ends within 120 days"
        if days < 0:
            action = f"Lapsed {-days} days ago: {int(rk['devices'].sum())} devices have no cover. Renew or re-tender now." if len(rk) else "Lapsed. Renew or re-tender now."
        elif r["auto_renew"] and notice >= TA:
            action = f"Renews automatically. Give notice by {notice:%d %b %Y} to renegotiate or exit."
        elif r["auto_renew"]:
            action = "Notice window has passed: it will renew automatically on the end date. Diarise the next notice date."
        else:
            action = "Does not renew automatically. Start renewal or re-tender now to avoid a gap in cover."
        rows.append(dict(contract_id=r["contract_id"], property_id=r["property_id"], contract_name=r["contract_name"], contract_kind=r["contract_kind"], supplier_name=r["supplier_name"],
                         covers=r["covers_device_types"] or "Property supply", end_date=end.date(), days_left=days, notice_deadline=notice.date(), auto_renew=bool(r["auto_renew"]),
                         annual_value_usd=r["annual_value_usd"], devices_covered=int(rk["devices"].sum()), failure_exposure_usd=exposure, status=status, action=action))
        if days <= 60:
            findings.append(("Critical" if days < 0 or days <= 14 else "High" if days <= 30 else "Medium", "Contract", r["property_id"],
                             f"{names[r['property_id']]}: {r['contract_name']} contract with {r['supplier_name']} " + (f"lapsed {-days} days ago" if days < 0 else f"ends in {days} days"),
                             action + (f" Predicted repair exposure over the next 30 days without cover: {_money(exposure)}." if exposure > 500 else ""), None, end.date()))
    out = pd.DataFrame(rows).sort_values("days_left")
    _save(con, "contract_reminders", out)


def run(con, run_id: str) -> None:
    print("Engineering and energy: models")
    cfg = load("energy.json")
    names = dict(con.execute("SELECT property_id, short_name FROM gold.dim_property").fetchall())
    findings, models = [], []
    energy_models(con, cfg, names, findings, models)
    device_models(con, cfg, names, findings, models)
    contract_reminders(con, names, findings)
    rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    f = pd.DataFrame(findings, columns=["priority", "category", "property_id", "title", "detail", "annual_value_usd", "due_date"])
    f = f.assign(_r=f["priority"].map(rank)).sort_values(["_r", "annual_value_usd"], ascending=[True, False]).drop(columns="_r").reset_index(drop=True)
    _save(con, "energy_findings", f)
    m = pd.DataFrame(models, columns=["model_name", "purpose", "method", "metric_name", "metric_value", "baseline_name", "baseline_value", "training_rows"])
    m["trained_at"], m["run_id"] = datetime.now(), run_id
    _save(con, "energy_models", m)
    for name, _, _, metric, val, bname, bval, _ in models:
        print(f"  {name:<46}{metric}: {val:,.3f}  ({bname}: {bval:,.3f})")
    print(f"  {len(f)} findings and reminders ({f['priority'].value_counts().to_dict()})")
