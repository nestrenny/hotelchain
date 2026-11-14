"""Energy & Assets page: AI predictions for energy management, device maintenance, and contract,
warranty and replacement reminders. Returns the same page spec the dashboards use, so the web
client renders it with the shared renderer, filters, export and row-level security."""
from __future__ import annotations

import pandas as pd
from fastapi import HTTPException

from hip.api.dashboards import Ctx, bar, div, fmtv, heat, page, table, tline
from hip.settings import AS_OF

A = f"DATE '{AS_OF}'"
VERTICAL = "Engineering & Energy"


def section(title: str, note: str) -> dict:
    return {"type": "section", "title": title, "note": note, "span": 12}


def tile(label, value, fmt, good="up", prev=None, prev_label=None, definition=None) -> dict:
    return {"label": label, "value": value, "prev": prev, "fmt": fmt, "good": good, "definition": definition, "prev_label": prev_label}


def energy(c: Ctx) -> dict:
    if not c.val("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'ai' AND table_name = 'energy_findings'"):
        raise HTTPException(503, "The engineering and energy data has not been built yet. Run: python -m hip.pipelines.run energy")
    P = c.P
    # ------------------------------------------------------------------ headline figures
    m = {}
    for prev in (False, True):
        r = c.con.execute(f"""SELECT sum(b.kwh), sum(b.expected_kwh), sum(b.kwh * r.tariff_usd_kwh), sum(b.kwh * r.carbon_kg_kwh) / 1000, sum(b.water_m3), sum(b.rooms_sold)
                              FROM ai.energy_baseline b JOIN ai.energy_rates r USING (property_id) WHERE {P('b.property_id')} AND {c.D('b.reading_date', prev)}""").fetchall()[0]
        for k, v in zip(("kwh", "exp", "cost", "co2", "water", "rooms"), r):
            m.setdefault(k, []).append(None if v is None else float(v))
    f30 = c.con.execute(f"SELECT sum(kwh), sum(cost_usd), sum(carbon_kg) / 1000 FROM ai.energy_forecast WHERE {P()}").fetchall()[0]
    l30 = c.val(f"SELECT sum(b.kwh * r.tariff_usd_kwh) FROM ai.energy_baseline b JOIN ai.energy_rates r USING (property_id) WHERE {P('b.property_id')} AND b.reading_date > {A} - INTERVAL 30 DAY")
    dev = c.con.execute(f"""SELECT count(*) FILTER (WHERE risk_band = 'High'), sum(failure_probability), count(*) FILTER (WHERE replace_reason <> ''), count(*),
                                   sum(replacement_cost_usd) FILTER (WHERE replace_reason <> '') FROM ai.device_risk WHERE {P()}""").fetchall()[0]
    fails30 = c.val(f"SELECT count(*) FROM gold.fact_work_orders WHERE {P()} AND work_type = 'Corrective' AND completed_date > {A} - INTERVAL 30 DAY")
    ctr = c.con.execute(f"SELECT count(*) FILTER (WHERE days_left < 0), count(*) FILTER (WHERE days_left BETWEEN 0 AND 90) FROM ai.contract_reminders WHERE {P()}").fetchall()[0]
    find = c.df(f"SELECT * FROM ai.energy_findings WHERE {P()}")
    saving = float(find["annual_value_usd"].fillna(0).sum())
    vs_exp = [div(m["kwh"][i], m["exp"][i]) - 1 if m["exp"][i] else None for i in (0, 1)]
    kpis = [
        tile("Energy use (MWh)", div(m["kwh"][0], 1000), "num", "down", div(m["kwh"][1], 1000), definition="Total metered electricity for the selected properties and period."),
        tile("Energy cost", m["cost"][0], "usd", "down", m["cost"][1], definition="Energy use multiplied by each property's tariff."),
        tile("Energy vs expected", vs_exp[0], "signed_pct", "down", vs_exp[1], definition="Actual energy use against what the baseline model expects for the occupancy and weather on the same days. Above zero means more energy than conditions justify."),
        tile("Energy per occupied room (kWh)", div(m["kwh"][0], m["rooms"][0]), "dec1", "down", div(m["kwh"][1], m["rooms"][1]), definition="Total energy use divided by occupied room nights. Allows properties of different sizes to be compared."),
        tile("Carbon emissions (tonnes CO2e)", m["co2"][0], "dec0", "down", m["co2"][1], definition="Energy use multiplied by each country's grid emission factor."),
        tile("Water use (m3)", m["water"][0], "num", "down", m["water"][1]),
        tile("Forecast energy cost, next 30 days", f30[1], "usd", "down", l30, "last 30 days", "Forecast energy use for the next 30 days at current tariffs, using the hotel occupancy forecast and the weather forecast, and assuming each property keeps running as it is now."),
        tile("Devices at high failure risk", dev[0], "num", "neutral", definition="Devices with a predicted probability of 40% or more of failing in the next 30 days."),
        tile("Expected failures, next 30 days", dev[1], "dec0", "down", fails30, "actual failures, last 30 days", "Sum of the failure probabilities of every device."),
        tile("Contracts lapsed or ending in 90 days", (ctr[0] or 0) + (ctr[1] or 0), "num", "neutral", definition="Service, lease, licence and supply contracts that have already ended or end within 90 days."),
        tile("Devices due for replacement", dev[2], "num", "neutral", definition="Devices past their design life and likely to fail, with under six months of estimated life left, or with repeated repairs above half their replacement cost."),
        tile("Annual savings and avoidable cost identified", saving, "usd", "neutral", definition="Sum of the annualised value of the open findings. An estimate based on stated assumptions."),
    ]

    # ------------------------------------------------------------------ energy
    act = c.df(f"SELECT reading_date AS d, sum(kwh) AS v, sum(expected_kwh) AS e FROM ai.energy_baseline WHERE {P()} AND {c.D('reading_date')} GROUP BY 1")
    fc = c.df(f"SELECT reading_date AS d, sum(kwh) AS v, sum(lower_80) AS lo, sum(upper_80) AS hi FROM ai.energy_forecast WHERE {P()} GROUP BY 1 ORDER BY 1") if c.live and c.grain == "day" else None
    eu = c.df(f"""SELECT p.short_name AS property, e.end_use, sum(e.kwh) / 1000 AS mwh FROM gold.fact_energy_daily e JOIN gold.dim_property p USING (property_id)
                  WHERE {P('e.property_id')} AND {c.D('e.reading_date')} GROUP BY ALL""")
    group = {"Guest room HVAC (occupied rooms)": "Guest room cooling and heating", "Guest room HVAC (vacant rooms)": "Guest room cooling and heating", "Public area HVAC": "Public area cooling and heating",
             "Lighting and power": "Lighting and power", "Hot water": "Hot water and laundry", "Laundry": "Hot water and laundry", "Kitchens": "Kitchens, pools and other", "Pools and waterpark": "Kitchens, pools and other",
             "Other": "Kitchens, pools and other"}
    eu["g"] = eu["end_use"].map(group)
    piv = eu.pivot_table(index="property", columns="g", values="mwh", aggfunc="sum").fillna(0)
    piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index].reset_index()
    perf = c.df(f"""SELECT p.short_name AS property, b.property_id, sum(b.kwh) / 1000 AS mwh, sum(b.kwh * r.tariff_usd_kwh) AS cost, sum(b.kwh) / sum(b.expected_kwh) - 1 AS vs_expected,
                           sum(b.kwh) / sum(b.rooms_sold) AS intensity, sum(b.kwh * r.carbon_kg_kwh) / 1000 AS carbon, any_value(a.status) AS status, any_value(a.driver_end_use) AS driver
                    FROM ai.energy_baseline b JOIN ai.energy_rates r USING (property_id) JOIN gold.dim_property p USING (property_id) JOIN ai.energy_anomalies a USING (property_id)
                    WHERE {P('b.property_id')} AND {c.D('b.reading_date')} GROUP BY ALL ORDER BY vs_expected DESC""")
    perf["driver"] = [d if s == "Anomaly" else "" for d, s in zip(perf["driver"], perf["status"])]
    waste = c.df(f"SELECT p.short_name AS property, w.setback_ratio, w.target_ratio, w.annual_saving_usd FROM ai.vacant_room_waste w JOIN gold.dim_property p USING (property_id) WHERE {P('w.property_id')} ORDER BY setback_ratio DESC")
    peak = c.df(f"""SELECT p.short_name AS property, k.contracted_kw, k.recent_peak_kw, k.predicted_peak_kw, k.predicted_peak_high_kw, k.headroom, k.peak_day, k.status
                    FROM ai.peak_demand k JOIN gold.dim_property p USING (property_id) WHERE {P('k.property_id')} ORDER BY headroom""")
    leak = c.df(f"""SELECT p.short_name AS property, w.property_id, w.baseline_night_m3_per_hour AS baseline, w.current_night_m3_per_hour AS current, w.status, w.suspected_since, w.loss_m3_per_day, w.annual_cost_usd, w.z_score
                    FROM ai.water_leak w JOIN gold.dim_property p USING (property_id) WHERE {P('w.property_id')} ORDER BY z_score DESC""")
    worst = leak.iloc[0]
    night = c.df(f"SELECT reading_date AS d, night_water_m3_per_hour AS v FROM ai.energy_baseline WHERE property_id = '{worst['property_id']}' AND reading_date > {A} - INTERVAL 75 DAY ORDER BY 1")
    panels = [
        section("Energy management", "What each property used, what it should have used for its occupancy and weather, and what it is expected to use next."),
        tline(c, "Energy use: actual, expected and forecast (kWh)", [("Actual", "actual", act[["d", "v"]]), ("Expected for occupancy and weather", "budget", act.rename(columns={"v": "x", "e": "v"})[["d", "v"]]),
                                                                    ("Forecast", "forecast", fc)], "num", 8,
              "The dashed grey line is the baseline model's expectation. A gap above it is energy that occupancy and weather do not explain. The forecast assumes each property keeps running as it is now."),
        bar("Energy by end use and property (MWh)", piv, "property", {g: g for g in piv.columns if g != "property"}, "num", 4, stacked=True),
        table("Energy performance by property", perf, [("property", "Property", "text"), ("mwh", "Energy (MWh)", "num"), ("cost", "Cost", "usd"), ("vs_expected", "vs expected", "signed_pct_bad"),
                                                       ("intensity", "kWh per occupied room", "dec1"), ("carbon", "Carbon (t CO2e)", "dec0"), ("status", "Last 30 days", "tag"), ("driver", "End use driving it", "text")], 12,
              "Status reflects the last 30 days: Anomaly means 5 or more days ran over 8% above the expected baseline."),
        bar("Vacant-room energy: empty room against occupied room", waste, "property", {"Energy of a vacant room as a share of an occupied room": "setback_ratio", "Target": "target_ratio"}, "pct", 6, horizontal=True,
            kinds={"Target": "budget"}, note="Empty rooms should be set back automatically. A high share means cooling or heating is running in rooms nobody is using."),
        table("Peak demand outlook, next 7 days", peak, [("property", "Property", "text"), ("contracted_kw", "Contracted (kW)", "num"), ("predicted_peak_kw", "Predicted peak (kW)", "num"),
                                                         ("recent_peak_kw", "Peak, last 30 days (kW)", "num"), ("predicted_peak_high_kw", "Upper estimate (kW)", "num"),
                                                         ("headroom", "Headroom", "signed_pct"), ("peak_day", "Peak day", "date"), ("status", "Status", "tag")], 12,
              "Exceeding the contracted supply capacity triggers demand charges. At risk means the upper estimate crosses the limit."),
        table("Water leak watch", leak, [("property", "Property", "text"), ("baseline", "Normal (m3/h)", "dec2"), ("current", "Now (m3/h)", "dec2"), ("status", "Status", "tag"),
                                         ("loss_m3_per_day", "Loss (m3/day)", "dec0")], 6,
              "Water use should fall to a trickle between 02:00 and 05:00. Flow that stays high overnight indicates a continuous leak."),
        tline(c, f"Overnight water flow at {worst['property']} (m3 per hour)", [("Overnight flow", "actual", night), ("Normal level", "budget", night.assign(v=worst["baseline"]))], "dec2", 12, bucket=False),
    ]
    panels[5], panels[6] = panels[6], panels[5]      # show the leak table beside the vacant-room chart, then the full-width peak table

    # ------------------------------------------------------------------ predictive maintenance
    risk = c.df(f"""SELECT d.asset_id, p.short_name AS property, d.device_type, d.location, d.age_years, d.failure_probability, d.remaining_life_months, d.drivers,
                           CASE WHEN d.under_warranty THEN 'In warranty' WHEN d.service_contract THEN 'Service contract' ELSE 'No cover' END AS cover, d.action
                    FROM ai.device_risk d JOIN gold.dim_property p USING (property_id) WHERE {P('d.property_id')} ORDER BY d.failure_probability DESC LIMIT 20""")
    bytype = c.df(f"SELECT device_type, sum(expected_failures) AS expected, sum(high_risk) AS high_risk FROM ai.maintenance_workload WHERE {P()} GROUP BY 1 ORDER BY 2 DESC")
    hm = c.df(f"""SELECT p.short_name AS property, d.device_type, count(*) FILTER (WHERE d.risk_band <> 'Low') AS n FROM ai.device_risk d JOIN gold.dim_property p USING (property_id)
                  WHERE {P('d.property_id')} GROUP BY ALL""")
    work = c.df(f"""SELECT device_type, sum(devices) AS devices, sum(high_risk) AS high_risk, sum(expected_failures) AS expected, sum(parts_to_stock) AS parts, any_value(part) AS part,
                           sum(technician_hours) AS hours, sum(expected_repair_cost_usd) AS cost FROM ai.maintenance_workload WHERE {P()} GROUP BY 1 ORDER BY cost DESC""")
    panels += [
        section("Predictive maintenance", "Which devices are likely to fail in the next 30 days, why, and what to prepare."),
        table("Devices most likely to fail in the next 30 days", risk, [("asset_id", "Device", "text"), ("device_type", "Type", "text"), ("property", "Property", "text"), ("location", "Location", "text"),
                                                                        ("age_years", "Age (years)", "dec1"), ("failure_probability", "Failure probability", "pct"), ("remaining_life_months", "Est. life left (months)", "dec0"),
                                                                        ("drivers", "Warning signs", "text"), ("cover", "Cover", "text"), ("action", "Recommended action", "tag")], 12,
              "Top 20 by predicted probability. Warning signs come from the device's own telemetry and service history."),
        bar("Expected failures in the next 30 days, by device type", bytype, "device_type", {"Expected failures": "expected"}, "dec1", 5, horizontal=True),
        heat("Devices at medium or high risk, by property and device type", hm, "property", "device_type", "n", "dec0", 7),
        table("Spares and technician workload, next 30 days", work, [("device_type", "Device type", "text"), ("devices", "Devices", "num"), ("high_risk", "High risk", "num"), ("expected", "Expected failures", "dec1"),
                                                                     ("parts", "Parts to stock", "num"), ("part", "Part most often needed", "text"), ("hours", "Technician hours", "dec0"), ("cost", "Expected repair cost", "usd")], 12,
              "Parts to stock adds a 20% buffer to the expected failures."),
    ]

    # ------------------------------------------------------------------ contracts, warranties, replacement
    con_ = c.df(f"""SELECT r.contract_id, p.short_name AS property, r.contract_name, r.supplier_name, r.covers, r.end_date, r.days_left, r.notice_deadline, r.annual_value_usd, r.devices_covered,
                           r.failure_exposure_usd, r.status, r.action FROM ai.contract_reminders r JOIN gold.dim_property p USING (property_id) WHERE {P('r.property_id')} ORDER BY r.days_left""")
    repl = c.df(f"""SELECT p.short_name AS property, r.device_type, r.replace_reason, r.devices, r.capex_usd, r.avg_age_years, r.repair_cost_12 FROM ai.replacement_plan r JOIN gold.dim_property p USING (property_id)
                    WHERE {P('r.property_id')} ORDER BY r.capex_usd DESC LIMIT 18""")
    capex = c.df(f"SELECT quarter, sum(capex_usd) AS capex, sum(devices) AS devices FROM ai.capex_forecast WHERE {P()} GROUP BY 1 ORDER BY 1")
    capex["quarter"] = capex["quarter"].str.replace("Q", " Q")
    warr = c.df(f"""SELECT p.short_name AS property, w.device_type, w.devices, w.first_expiry, w.days_left, w.at_risk, w.action FROM ai.warranty_reminders w JOIN gold.dim_property p USING (property_id)
                    WHERE {P('w.property_id')} ORDER BY w.days_left""")
    models = c.df("SELECT model_name, purpose, method, metric_name, metric_value, baseline_name, baseline_value FROM ai.energy_models")
    fmt_m = lambda n, v: f"{v:.1%}" if ("WAPE" in n or "rate" in n.lower()) else f"{v:.2f}" if "AUC" in n else f"{v:,.0f}"  # noqa: E731
    models["result"] = [f"{n}: {fmt_m(n, v)}" for n, v in zip(models["metric_name"], models["metric_value"])]
    models["compared_with"] = [f"{bn}: {fmt_m(n, bv)}" for n, bn, bv in zip(models["metric_name"], models["baseline_name"], models["baseline_value"])]
    panels += [
        section("Contracts, warranties and replacement", "Dates that need action, with the predicted failure exposure if cover lapses."),
        table("Contract reminders", con_, [("contract_name", "Contract", "text"), ("property", "Property", "text"), ("supplier_name", "Supplier", "text"), ("covers", "Covers", "text"), ("end_date", "Ends", "date"),
                                           ("days_left", "Days left", "num"), ("notice_deadline", "Notice by", "date"), ("annual_value_usd", "Annual value", "usd"), ("devices_covered", "Devices", "num"),
                                           ("failure_exposure_usd", "30-day repair exposure", "usd"), ("status", "Status", "tag"), ("action", "What to do", "text")], 12,
              "Contracts that have lapsed or end within 120 days. Repair exposure is the predicted cost of failures among the covered devices over the next 30 days, which the property would bear without cover."),
        table("Devices due for replacement", repl, [("property", "Property", "text"), ("device_type", "Device type", "text"), ("replace_reason", "Reason", "text"), ("devices", "Devices", "num"),
                                                    ("capex_usd", "Replacement cost", "usd"), ("avg_age_years", "Avg age (years)", "dec1")], 7,
              "Estimated life left is design life less age, shortened by measured efficiency loss. It is an engineering estimate, not a trained model."),
        bar("Replacement capital forecast by quarter", capex, "quarter", {"Replacement cost": "capex"}, "usd", 5, note="When the flagged devices are expected to reach end of life."),
        table("Warranties ending within 90 days", warr, [("property", "Property", "text"), ("device_type", "Device type", "text"), ("devices", "Devices", "num"), ("first_expiry", "First expiry", "date"),
                                                         ("at_risk", "With fault signs", "num")], 5,
              "Inspect these devices, and raise claims for any showing fault signs, while the manufacturer still bears the cost."),
        table("Models behind this page", models, [("model_name", "Model", "text"), ("purpose", "What it predicts", "text"), ("method", "Method", "text"), ("result", "Result", "text"), ("compared_with", "Compared with", "text")], 7,
              "Measured on synthetic data. These models are not yet listed in the central Model Monitoring page."),
    ]

    # ------------------------------------------------------------------ plain-language summary and reminders
    anomalies = perf[perf["status"] == "Anomaly"]
    leaks = leak[leak["status"] != "Normal"]
    insights = [
        {"text": f"Energy use is {abs(vs_exp[0]):.1%} {'above' if vs_exp[0] > 0 else 'below'} what the occupancy and weather justify for the period." if vs_exp[0] is not None else None,
         "tone": "bad" if (vs_exp[0] or 0) > 0.03 else "good", "measure": "Energy vs expected"},
        {"text": f"{', '.join(anomalies['property'])}: energy anomaly in the last 30 days, driven by {anomalies.iloc[0]['driver']}." if len(anomalies) else "No property is running above its expected energy baseline.",
         "tone": "bad" if len(anomalies) else "good", "measure": "Energy anomaly detection"},
        {"text": f"The next 30 days are forecast to use {f30[0] / 1000:,.0f} MWh, costing {fmtv(f30[1], 'usd')} and emitting {f30[2]:,.0f} tonnes of CO2e.", "tone": "neutral", "measure": "Energy forecast"},
        {"text": f"{dev[0]} devices have a high probability of failing within 30 days; about {dev[1]:.0f} failures are expected across {dev[3]:,} devices.", "tone": "bad" if dev[0] else "good", "measure": "Device failure prediction"},
        {"text": f"Probable water leak at {', '.join(leaks['property'])}: about {leaks['loss_m3_per_day'].sum():,.0f} m3 a day." if len(leaks) else "Overnight water flow is normal at every property.",
         "tone": "bad" if len(leaks) else "good", "measure": "Water leak detection"},
        {"text": f"{ctr[0] or 0} contract(s) have lapsed and {ctr[1] or 0} end within 90 days; {dev[2]:,} devices are due for replacement ({fmtv(dev[4] or 0, 'usd')}).", "tone": "bad" if ctr[0] else "neutral",
         "measure": "Contracts and lifecycle"},
    ]
    spec = page(c, "Energy & Assets", "AI predictions for energy management, device maintenance, contracts and replacement", kpis, panels, insights, VERTICAL)
    names = c.names
    spec["actions_title"] = "Findings and reminders"
    spec["actions"] = [{"priority": r["priority"], "category": r["category"], "property": names.get(r["property_id"], r["property_id"]), "title": r["title"], "detail": r["detail"],
                        "value": None if pd.isna(r["annual_value_usd"]) else float(r["annual_value_usd"]), "due_date": None if pd.isna(r["due_date"]) else r["due_date"]} for _, r in find.iterrows()]
    return spec
