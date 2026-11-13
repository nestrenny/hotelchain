"""Demand and financial forecasting (SRS 7.2, HOTEL-FORECAST-001).

One gradient-boosted model per series on calendar, seasonality, event and same-day-last-year
features, with a recent-level correction. Each series is backtested on the last 90 days against a
seasonal-naive challenger; the better one is promoted. 80% intervals come from backtest errors.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from hip.settings import AS_OF, FORECAST_HORIZON, HISTORY_START

T0 = pd.Timestamp(HISTORY_START)
TA = pd.Timestamp(AS_OF)
HIST = pd.date_range(T0, TA)
FUT = pd.date_range(TA + pd.Timedelta(days=1), periods=FORECAST_HORIZON)

# model family -> (name, vertical, objective, unit, series SQL returning property_id, entity_id, entity_name, d, y)
FAMILIES = {
    "hotel_rooms_sold": ("Hotel Occupancy Forecast", "Hotels", "Forecast occupied rooms by property and room category for the next 30/60/90 days to steer pricing, inventory and promotions.", "rooms", """
        SELECT property_id, room_type AS entity_id, room_type AS entity_name, business_date AS d, rooms_sold AS y FROM gold.fact_room_occupancy WHERE is_actual
        UNION ALL SELECT property_id, 'ALL', 'All room categories', business_date, sum(rooms_sold) FROM gold.fact_room_occupancy WHERE is_actual GROUP BY ALL"""),
    "hotel_room_revenue": ("Room Revenue Forecast", "Hotels", "Forecast daily room revenue by property to support revenue planning and forecast-versus-actual tracking.", "USD", """
        SELECT property_id, 'ALL' AS entity_id, 'Room revenue' AS entity_name, business_date AS d, sum(room_revenue_usd) AS y FROM gold.fact_room_occupancy WHERE is_actual GROUP BY ALL"""),
    "restaurant_covers": ("Restaurant Covers Forecast", "Restaurants", "Forecast covers by outlet so that staffing, table allocation and food ordering match demand.", "covers", """
        SELECT c.property_id, c.outlet_code AS entity_id, o.outlet_name AS entity_name, c.business_date AS d, sum(c.covers) AS y
        FROM gold.fact_restaurant_covers c JOIN gold.dim_outlet o USING (outlet_code) GROUP BY ALL"""),
    "restaurant_spend_per_guest": ("Guest Spend Prediction", "Restaurants", "Predict average spend per cover by outlet to plan revenue and target upselling.", "USD", """
        SELECT c.property_id, c.outlet_code AS entity_id, o.outlet_name AS entity_name, c.business_date AS d, sum(c.net_usd) / sum(c.covers) AS y
        FROM gold.fact_restaurant_covers c JOIN gold.dim_outlet o USING (outlet_code) GROUP BY ALL"""),
    "waterpark_attendance": ("Waterpark Attendance Forecast", "Waterpark", "Forecast daily visitors by park to manage capacity, staffing and visitor experience.", "visitors", """
        SELECT w.property_id, w.park_code AS entity_id, o.outlet_name AS entity_name, w.business_date AS d, w.visitors AS y FROM gold.fact_waterpark_daily w JOIN gold.dim_outlet o ON o.outlet_code = w.park_code"""),
    "retail_units": ("Retail Demand Forecast", "Retail", "Forecast unit demand by store and category to drive replenishment and avoid stock-outs.", "units", """
        SELECT s.property_id, s.store_code || '|' || p.category AS entity_id, o.outlet_name || ' - ' || p.category AS entity_name, s.business_date AS d, sum(s.qty) AS y
        FROM gold.fact_retail_sales s JOIN silver.product p USING (sku) JOIN gold.dim_outlet o ON o.outlet_code = s.store_code GROUP BY ALL"""),
    "spa_appointments": ("Spa Appointment Forecast", "Spa & Leisure", "Forecast completed appointments by spa to optimise therapist rosters and fill unused capacity.", "appointments", """
        SELECT a.property_id, a.spa_code AS entity_id, o.outlet_name AS entity_name, a.business_date AS d, count(*) AS y
        FROM gold.fact_spa_appointments a JOIN gold.dim_outlet o ON o.outlet_code = a.spa_code WHERE a.status = 'COMPLETED' GROUP BY ALL"""),
    "finance_revenue": ("Total Revenue Forecast", "Finance", "Forecast total operating revenue by property for financial planning.", "USD", """
        SELECT property_id, 'ALL' AS entity_id, 'Operating revenue' AS entity_name, business_date AS d, gl_revenue_usd AS y FROM gold.mart_executive_daily"""),
    "finance_opex": ("Operating Expenditure Forecast", "Finance", "Forecast operating expenditure by property to anticipate budget variance.", "USD", """
        SELECT property_id, 'ALL' AS entity_id, 'Operating expenditure' AS entity_name, business_date AS d, opex_usd AS y FROM gold.mart_executive_daily"""),
    "finance_gop": ("Profitability Forecast", "Finance", "Forecast gross operating profit by property to flag profitability risk early.", "USD", """
        SELECT property_id, 'ALL' AS entity_id, 'Gross operating profit' AS entity_name, business_date AS d, gop_usd AS y FROM gold.mart_executive_daily"""),
}
FEATURES = ["day_of_week", "month", "is_weekend", "annual_sin_1", "annual_cos_1", "annual_sin_2", "annual_cos_2", "annual_sin_3", "annual_cos_3",
            "trend_years", "is_event", "same_weekday_last_year"]


def features(dates: pd.DatetimeIndex, events: set, y: pd.Series) -> np.ndarray:
    doy = dates.day_of_year.values / 365.25 * 2 * np.pi
    cols = [dates.day_of_week.values, dates.month.values, (dates.day_of_week.values >= 5).astype(int)]
    for k in (1, 2, 3):
        cols += [np.sin(k * doy), np.cos(k * doy)]
    cols += [(dates - T0).days.values / 365.0, np.array([d in events for d in dates], dtype=int), y.reindex(dates - pd.Timedelta(days=364)).values]
    return np.column_stack(cols).astype(float)


def seasonal_naive(y: pd.Series, dates: pd.DatetimeIndex) -> np.ndarray:
    ly = y.reindex(dates - pd.Timedelta(days=364)).values
    last = y.iloc[-28:]
    growth = last.mean() / max(y.reindex(last.index - pd.Timedelta(days=364)).mean(), 1e-9)
    growth = growth if np.isfinite(growth) else 1.0
    return np.where(np.isnan(ly), last.mean(), ly * np.clip(growth, 0.7, 1.4))


def gbm_forecast(y: pd.Series, dates: pd.DatetimeIndex, events: set) -> np.ndarray:
    model = HistGradientBoostingRegressor(max_iter=160, learning_rate=0.07, max_depth=5, min_samples_leaf=8, l2_regularization=1.0, random_state=0)
    model.fit(features(y.index, events, y), y.values)
    pred = model.predict(features(dates, events, y))
    fitted = model.predict(features(y.index[-28:], events, y))
    ratio = np.clip(y.iloc[-28:].sum() / max(fitted.sum(), 1e-9), 0.8, 1.25) if fitted.sum() > 0 else 1.0
    h = np.arange(1, len(dates) + 1)
    return pred * ratio ** np.exp(-h / 60.0)        # recent-level correction fades with horizon


def wape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.abs(actual - pred).sum() / max(np.abs(actual).sum(), 1e-9))


def run(con, run_id: str) -> list[dict]:
    """Train, backtest and forecast every series. Returns one registry record per model family."""
    now = datetime.now()
    ev = con.execute("SELECT property_id, event_date FROM gold.dim_event_calendar").df()
    events = {p: set(pd.to_datetime(g["event_date"])) for p, g in ev.groupby("property_id")}
    cap_rooms = con.execute("SELECT property_id, room_type AS entity_id, business_date AS d, rooms_available AS cap, rooms_on_books AS otb FROM gold.fact_room_occupancy").df()
    cap_all = cap_rooms.groupby(["property_id", "d"], as_index=False)[["cap", "otb"]].sum().assign(entity_id="ALL")
    cap_rooms = pd.concat([cap_rooms, cap_all])
    cap_rooms["d"] = pd.to_datetime(cap_rooms["d"])
    caps = {k: g.set_index("d") for k, g in cap_rooms.groupby(["property_id", "entity_id"])}
    static_cap = dict(con.execute("SELECT outlet_code, CASE WHEN outlet_type = 'Restaurant' THEN capacity * turns_per_day WHEN outlet_type = 'Spa' THEN capacity * 7 ELSE capacity END FROM gold.dim_outlet").fetchall())
    split = TA - pd.Timedelta(days=FORECAST_HORIZON)
    bt_dates = pd.date_range(split + pd.Timedelta(days=1), TA)
    rows, acc, registry = [], [], []
    for fam, (name, vertical, objective, unit, sql) in FAMILIES.items():
        df = con.execute(sql).df()
        df["d"] = pd.to_datetime(df["d"])
        fam_acc = []
        for (pid, eid, ename), g in df.groupby(["property_id", "entity_id", "entity_name"]):
            y = g.set_index("d")["y"].astype(float).reindex(HIST).fillna(0.0)
            evs = events.get(pid, set())
            cap_f = cap_b = otb = None
            if fam == "hotel_rooms_sold":
                c = caps[(pid, eid)]
                cap_f, cap_b, otb = c["cap"].reindex(FUT).values, c["cap"].reindex(bt_dates).values, c["otb"].reindex(FUT).fillna(0).values
            elif eid in static_cap:
                cap_f, cap_b = np.full(len(FUT), float(static_cap[eid])), np.full(len(bt_dates), float(static_cap[eid]))
            # ---- backtest: train to the split date, score the following 90 days ----
            ytr, act = y[:split], y[bt_dates].values
            b_gbm, b_naive = np.maximum(gbm_forecast(ytr, bt_dates, evs), 0) if fam != "finance_gop" else gbm_forecast(ytr, bt_dates, evs), seasonal_naive(ytr, bt_dates)
            if fam == "hotel_rooms_sold":
                b_gbm, b_naive = np.minimum(b_gbm, cap_b), np.minimum(b_naive, cap_b)
            w_gbm, w_naive = wape(act, b_gbm), wape(act, b_naive)
            champion = "Gradient boosting" if w_gbm <= w_naive else "Seasonal naive"
            bt = b_gbm if champion == "Gradient boosting" else b_naive
            additive = fam == "finance_gop"      # profit can sit near zero, so its interval is built from absolute errors
            rel = (act - bt) if additive else (act - bt) / np.maximum(np.abs(bt), max(np.abs(act).mean() * 0.05, 1e-6))
            floor = 0.0 if additive else 0.03
            q = [(min(np.quantile(rel[s], 0.10), -floor), max(np.quantile(rel[s], 0.90), floor)) for s in (slice(0, 30), slice(30, 60), slice(60, 90))]
            lo_m, hi_m = np.repeat([a for a, _ in q], 30), np.repeat([b for _, b in q], 30)
            half = len(rel) // 2
            bins = np.quantile(rel, [0, .2, .4, .6, .8, 1.0])
            bins[0], bins[-1] = -np.inf, np.inf
            pa = np.clip(np.histogram(rel[:half], bins)[0] / half, 0.02, None)
            pb = np.clip(np.histogram(rel[half:], bins)[0] / (len(rel) - half), 0.02, None)
            # PSI on two 45-day samples is biased upwards by about (bins-1)*(1/n1+1/n2); remove that sampling noise
            psi = max(float(((pb - pa) * np.log(pb / pa)).sum()) - 4 * (1 / half + 1 / (len(rel) - half)), 0.0)
            # ---- production forecast: retrain on everything through the last closed date ----
            fc = gbm_forecast(y, FUT, evs) if champion == "Gradient boosting" else seasonal_naive(y, FUT)
            if fam != "finance_gop":
                fc = np.maximum(fc, 0)
            sb, sf = (1.0, 1.0) if additive else (np.abs(bt), np.abs(fc))
            lo, hi = fc + sf * lo_m, fc + sf * hi_m
            if fam == "hotel_rooms_sold":
                fc = np.minimum(np.maximum(fc, 0.9 * otb), cap_f)        # never below what is already on the books, never above sellable rooms
                lo, hi = np.minimum(np.maximum(lo, 0.8 * otb), fc), np.minimum(np.maximum(hi, fc), cap_f)
            base = dict(model_id=fam, metric=fam, business_vertical=vertical, property_id=pid, entity_id=eid, entity_name=ename, unit=unit, algorithm=champion)
            for i, d in enumerate(bt_dates):
                rows.append({**base, "business_date": d.date(), "forecast_type": "backtest", "horizon_day": i + 1, "forecast_value": bt[i], "lower_80": (bt + sb * lo_m)[i],
                             "upper_80": (bt + sb * hi_m)[i], "actual_value": act[i], "capacity": None if cap_b is None else cap_b[i]})
            for i, d in enumerate(FUT):
                rows.append({**base, "business_date": d.date(), "forecast_type": "forecast", "horizon_day": i + 1, "forecast_value": fc[i], "lower_80": lo[i], "upper_80": hi[i],
                             "actual_value": None, "capacity": None if cap_f is None else cap_f[i]})
            cover = float(((act >= bt + sb * lo_m) & (act <= bt + sb * hi_m)).mean())
            rec = dict(model_id=fam, property_id=pid, entity_id=eid, entity_name=ename, champion=champion, wape=min(w_gbm, w_naive), wape_gbm=w_gbm, wape_naive=w_naive,
                       bias=float((bt - act).sum() / max(np.abs(act).sum(), 1e-9)), wape_h30=wape(act[:30], bt[:30]), wape_h60=wape(act[30:60], bt[30:60]), wape_h90=wape(act[60:], bt[60:]),
                       interval_coverage=cover, drift_psi=psi, actual_total=float(np.abs(act).sum()))
            acc.append(rec)
            fam_acc.append(rec)
        a = pd.DataFrame(fam_acc)
        wts = a["actual_total"] / a["actual_total"].sum()
        psi = float((a["drift_psi"] * wts).sum())
        registry.append(dict(model_id=fam, model_name=name, business_vertical=vertical, business_objective=objective, model_type="Forecast",
                             algorithm="Gradient boosting (HistGradientBoostingRegressor) with seasonal-naive challenger", features=", ".join(FEATURES),
                             training_rows=int(len(HIST) * len(a)), training_from=T0.date(), training_to=TA.date(), series_count=int(len(a)), metric_name="WAPE (90-day backtest)",
                             metric_value=float((a["wape"] * wts).sum()), baseline_name="Seasonal-naive WAPE", baseline_value=float((a["wape_naive"] * wts).sum()),
                             forecast_error=float((a["wape"] * wts).sum()), drift_score=psi, trained_at=now,
                             explainability=f"{int((a['champion'] == 'Gradient boosting').sum())} of {len(a)} series use gradient boosting; the rest fall back to seasonal naive. "
                                            f"80% interval coverage in backtest: {a['interval_coverage'].mean():.0%}."))
        print(f"  forecast {fam:<28}{len(a):>3} series  WAPE {registry[-1]['metric_value']:.1%}  (naive {registry[-1]['baseline_value']:.1%})")
    f = pd.DataFrame(rows)
    f["generated_at"] = now
    con.register("_fc", f)
    con.execute("CREATE OR REPLACE TABLE gold.fact_forecasts AS SELECT * FROM _fc")
    con.register("_acc", pd.DataFrame(acc))
    con.execute("CREATE OR REPLACE TABLE ai.forecast_accuracy AS SELECT * FROM _acc")
    return registry
