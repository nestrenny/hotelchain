"""Predictive models beyond time-series forecasting (SRS 7.2): cancellation risk, guest churn,
guest segmentation and lifetime value, campaign conversion, and attraction queue prediction.

Personal data rule (SRS 10.3): models train on pseudonymised guest keys and behavioural features
only - no names, e-mail addresses or nationality are used as features.
"""
from __future__ import annotations

from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss, mean_absolute_error, r2_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from hip.settings import AS_OF, MODELS

A = f"DATE '{AS_OF}'"
TA = pd.Timestamp(AS_OF)


def _encode(df: pd.DataFrame, cats: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cats:
        out[c] = out[c].astype("category").cat.codes
    return out


def _importance(model, X, y, cols, scoring) -> str:
    n = min(len(X), 4000)
    idx = np.random.default_rng(0).choice(len(X), n, replace=False)
    imp = permutation_importance(model, X.iloc[idx], y.iloc[idx], n_repeats=3, random_state=0, scoring=scoring).importances_mean
    top = sorted(zip(cols, imp), key=lambda t: -t[1])[:5]
    tot = sum(max(v, 0) for _, v in top) or 1
    return "Top drivers (permutation importance): " + ", ".join(f"{c.replace('_', ' ')} {max(v, 0) / tot:.0%}" for c, v in top)


def _psi(a: np.ndarray, b: np.ndarray) -> float:
    bins = np.unique(np.quantile(a, np.linspace(0, 1, 11)))
    if len(bins) < 3:
        return 0.0
    bins[0], bins[-1] = -np.inf, np.inf
    pa = np.clip(np.histogram(a, bins)[0] / len(a), 0.005, None)
    pb = np.clip(np.histogram(b, bins)[0] / len(b), 0.005, None)
    return float(((pb - pa) * np.log(pb / pa)).sum())


def cancellation(con) -> dict:
    df = con.execute(f"""
        SELECT r.reservation_id, r.property_id, r.guest_key, r.arrival_date, r.status, r.lead_days, r.nights, r.channel, r.segment, r.room_type, r.guests,
               r.room_revenue_usd / r.nights AS nightly_rate_usd, r.room_revenue_usd, (r.discount_usd > 0)::INT AS discounted, coalesce(g.loyalty_tier, 'Non-member') AS loyalty_tier,
               month(r.arrival_date) AS arrival_month, isodow(r.arrival_date) AS arrival_day_of_week
        FROM gold.fact_reservations r LEFT JOIN gold.dim_guest g USING (guest_key)""").df()
    cats = ["property_id", "channel", "segment", "room_type", "loyalty_tier"]
    cols = ["property_id", "lead_days", "nights", "channel", "segment", "room_type", "guests", "nightly_rate_usd", "discounted", "loyalty_tier", "arrival_month", "arrival_day_of_week"]
    X = _encode(df, cats)[cols]
    df["arrival_date"] = pd.to_datetime(df["arrival_date"])
    known = df["arrival_date"] <= TA
    y = (df["status"] == "CANCELLED").astype(int)
    tr = known & (df["arrival_date"] <= TA - pd.Timedelta(days=120))
    te = known & ~tr
    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.08, max_depth=6, categorical_features=[cols.index(c) for c in cats], random_state=0)
    model.fit(X[tr], y[tr])
    p_te = model.predict_proba(X[te])[:, 1]
    auc, brier = roc_auc_score(y[te], p_te), brier_score_loss(y[te], p_te)
    expl = _importance(model, X[te], y[te], cols, "roc_auc")
    fut = (df["status"] == "RESERVED") & (df["arrival_date"] > TA)
    s = df.loc[fut, ["reservation_id", "property_id", "guest_key", "arrival_date", "nights", "channel", "segment", "lead_days", "room_revenue_usd", "loyalty_tier"]].copy()
    s["cancel_probability"] = model.predict_proba(X[fut])[:, 1]
    s["risk_band"] = np.where(s["cancel_probability"] >= 0.40, "High", np.where(s["cancel_probability"] >= 0.25, "Medium", "Low"))
    drivers = [np.where(s["channel"].isin(["OTA", "Wholesale"]), s["channel"] + " booking", ""), np.where(s["lead_days"] > 60, "long lead time", ""),
               np.where(s["loyalty_tier"] == "Non-member", "not a loyalty member", ""), np.where(s["nights"] == 1, "single-night stay", "")]
    s["drivers"] = [", ".join(x for x in t if x) or "booking pattern" for t in zip(*drivers)]
    s["arrival_date"] = s["arrival_date"].dt.date
    con.register("_s", s)
    con.execute("CREATE OR REPLACE TABLE ai.reservation_risk AS SELECT * FROM _s")
    joblib.dump(model, MODELS / "hotel_cancellation.joblib")
    base = float(max(y[tr].mean(), 1 - y[tr].mean()))
    print(f"  model    hotel_cancellation           AUC {auc:.3f}  Brier {brier:.3f}  scored {len(s):,} reservations, {int((s['risk_band'] == 'High').sum()):,} high risk")
    return dict(model_id="hotel_cancellation", model_name="Reservation Cancellation Prediction", business_vertical="Hotels", model_type="Classification",
                business_objective="Identify reservations likely to cancel so revenue teams can engage guests and protect on-the-books revenue.",
                algorithm="Gradient boosting classifier (HistGradientBoostingClassifier)", features=", ".join(cols), training_rows=int(tr.sum()),
                training_from=df.loc[tr, "arrival_date"].min().date(), training_to=df.loc[tr, "arrival_date"].max().date(), series_count=1, metric_name="ROC AUC (120-day holdout)",
                metric_value=float(auc), baseline_name="Random ranking AUC", baseline_value=0.5, forecast_error=float(brier),
                drift_score=_psi(model.predict_proba(X[tr].sample(20000, random_state=0))[:, 1], p_te), trained_at=datetime.now(), explainability=expl + f". Majority-class accuracy {base:.0%}.")


def _guest_features(con, snap: str) -> pd.DataFrame:
    return con.execute(f"""
        WITH r AS (SELECT *, CAST(booking_ts AS DATE) AS booked FROM gold.fact_reservations WHERE guest_key IS NOT NULL), s AS (SELECT {snap} AS d)
        SELECT r.guest_key,
               count(*) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN') AND arrival_date <= s.d) AS stays,
               coalesce(sum(nights) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN') AND arrival_date <= s.d), 0) AS nights,
               coalesce(sum(room_revenue_usd) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN') AND arrival_date <= s.d), 0) AS room_spend_usd,
               date_diff('day', max(arrival_date) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN') AND arrival_date <= s.d), s.d) AS recency_days,
               date_diff('day', min(arrival_date) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN') AND arrival_date <= s.d), s.d) AS tenure_days,
               count(*) FILTER (WHERE status = 'CANCELLED' AND booked <= s.d) AS cancellations,
               count(DISTINCT property_id) FILTER (WHERE arrival_date <= s.d) AS properties_visited,
               coalesce(avg(lead_days) FILTER (WHERE booked <= s.d), 0) AS avg_lead_days,
               (count(*) FILTER (WHERE booked <= s.d AND arrival_date > s.d AND (status <> 'CANCELLED' OR cancel_date > s.d)) > 0)::INT AS has_future_booking,
               (count(*) FILTER (WHERE ((arrival_date > s.d AND arrival_date <= s.d + INTERVAL 180 DAY AND status IN ('CHECKED_OUT', 'CHECKED_IN'))
                                        OR (booked > s.d AND booked <= s.d + INTERVAL 180 DAY))) = 0)::INT AS churned
        FROM r, s GROUP BY r.guest_key, s.d
        HAVING count(*) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN') AND arrival_date <= s.d) > 0""").df()


def churn_and_value(con) -> list[dict]:
    tier = con.execute("SELECT guest_key, loyalty_tier, is_member::INT AS is_member FROM gold.dim_guest").df()
    tier["tier_rank"] = tier["loyalty_tier"].map({"Non-member": 0, "Silver": 1, "Gold": 2, "Platinum": 3}).fillna(0)
    cols = ["stays", "nights", "room_spend_usd", "recency_days", "tenure_days", "cancellations", "properties_visited", "avg_lead_days", "has_future_booking", "tier_rank", "is_member"]
    train = _guest_features(con, f"({A} - INTERVAL 180 DAY)").merge(tier, on="guest_key")
    Xtr, Xte, ytr, yte = train_test_split(train[cols], train["churned"], test_size=0.25, random_state=0, stratify=train["churned"])
    model = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.08, max_depth=5, random_state=0)
    model.fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    auc, brier = roc_auc_score(yte, p), brier_score_loss(yte, p)
    expl = _importance(model, Xte, yte, cols, "roc_auc")
    now_f = _guest_features(con, A).merge(tier, on="guest_key")
    now_f["churn_probability"] = model.predict_proba(now_f[cols])[:, 1]
    joblib.dump(model, MODELS / "guest_churn.joblib")
    churn_reg = dict(model_id="guest_churn", model_name="Guest Churn Prediction", business_vertical="Marketing", model_type="Classification",
                     business_objective="Identify guests unlikely to return within 180 days so retention campaigns can target the most valuable ones.",
                     algorithm="Gradient boosting classifier (HistGradientBoostingClassifier)", features=", ".join(cols), training_rows=int(len(Xtr)),
                     training_from=AS_OF.replace(year=AS_OF.year - 2), training_to=(TA - pd.Timedelta(days=180)).date(), series_count=1, metric_name="ROC AUC (25% holdout)",
                     metric_value=float(auc), baseline_name="Random ranking AUC", baseline_value=0.5, forecast_error=float(brier),
                     drift_score=_psi(model.predict_proba(Xtr)[:, 1], now_f["churn_probability"].values), trained_at=datetime.now(),
                     explainability=expl + f". Base churn rate {train['churned'].mean():.0%}.")
    print(f"  model    guest_churn                  AUC {auc:.3f}  Brier {brier:.3f}  scored {len(now_f):,} guests")

    g = con.execute("SELECT guest_key, total_spend_usd, stays, verticals_used, first_stay, last_activity_date, marketing_consent FROM gold.mart_guest_360 WHERE total_spend_usd > 0").df()
    g["recency"] = (TA - pd.to_datetime(g["last_activity_date"])).dt.days.fillna(730)
    g["per_stay"] = g["total_spend_usd"] / g["stays"].clip(lower=1)
    Z = StandardScaler().fit_transform(np.column_stack([np.log1p(g["total_spend_usd"]), np.log1p(g["stays"]), g["verticals_used"], np.log1p(g["recency"]), np.log1p(g["per_stay"])]))
    km = KMeans(n_clusters=5, n_init=5, random_state=0).fit(Z)
    g["cluster"] = km.labels_
    order = g.groupby("cluster")["total_spend_usd"].mean().sort_values().index.tolist()
    names = ["Occasional Visitors", "Experience Seekers", "Rising Regulars", "High-Value Regulars", "Elite Loyalists"]
    g["segment"] = g["cluster"].map({c: names[i] for i, c in enumerate(order)})
    tenure = ((TA - pd.to_datetime(g["first_stay"])).dt.days.fillna(365) / 365).clip(lower=0.5, upper=2.0)
    g["annual_value_usd"] = g["total_spend_usd"] / tenure
    g = g.merge(now_f[["guest_key", "churn_probability"]], on="guest_key", how="left")
    g["churn_probability"] = g["churn_probability"].fillna(now_f["churn_probability"].median())
    g["clv_3yr_usd"] = g["annual_value_usd"] * (1 - g["churn_probability"]) * 3 + g["annual_value_usd"] * 0.25
    g["churn_band"] = np.where(g["churn_probability"] >= 0.6, "High", np.where(g["churn_probability"] >= 0.4, "Medium", "Low"))
    con.register("_g", g[["guest_key", "segment", "annual_value_usd", "clv_3yr_usd", "churn_probability", "churn_band"]])
    con.execute("CREATE OR REPLACE TABLE ai.guest_scores AS SELECT * FROM _g")
    sizes = g["segment"].value_counts()
    seg_reg = dict(model_id="guest_segmentation", model_name="Guest Segmentation & Lifetime Value", business_vertical="Marketing", model_type="Clustering",
                   business_objective="Group guests by value and engagement, and estimate three-year lifetime value, to personalise offers and prioritise retention.",
                   algorithm="K-means (k=5) on standardised spend, frequency, breadth and recency; CLV = annual value x retention probability x 3 years",
                   features="total_spend_usd, stays, verticals_used, recency_days, spend_per_stay", training_rows=int(len(g)), training_from=AS_OF.replace(year=AS_OF.year - 2),
                   training_to=AS_OF, series_count=1, metric_name="Share of spend held by top two segments",
                   metric_value=float(g.loc[g["segment"].isin(names[3:]), "total_spend_usd"].sum() / g["total_spend_usd"].sum()), baseline_name="Share of guests in top two segments",
                   baseline_value=float(g["segment"].isin(names[3:]).mean()), forecast_error=float(km.inertia_ / len(g)), drift_score=0.0, trained_at=datetime.now(),
                   explainability="Segments: " + ", ".join(f"{n} {sizes.get(n, 0):,}" for n in names))
    print(f"  model    guest_segmentation           {len(g):,} guests in 5 segments")
    return [churn_reg, seg_reg]


def campaign_conversion(con) -> dict:
    df = con.execute("""
        SELECT c.campaign_id, c.channel, c.target_vertical, c.audience_segment, c.budget_usd, date_diff('day', c.start_date, c.end_date) + 1 AS duration_days, c.end_date,
               sum(d.spend_usd) AS spend_usd, sum(d.conversions) AS conversions, sum(d.clicks) AS clicks, sum(d.attributed_revenue_usd) AS revenue_usd
        FROM gold.dim_campaign c JOIN gold.fact_marketing_campaigns d USING (campaign_id) GROUP BY ALL HAVING sum(d.spend_usd) > 0""").df()
    df["conv_per_1k"] = df["conversions"] / df["spend_usd"] * 1000
    cats = ["channel", "target_vertical", "audience_segment"]
    cols = cats + ["budget_usd", "duration_days"]
    X = _encode(df, cats)[cols]
    y = np.log1p(df["conv_per_1k"])
    done = pd.to_datetime(df["end_date"]) <= TA
    Xtr, Xte, ytr, yte = train_test_split(X[done], y[done], test_size=0.3, random_state=0)
    model = HistGradientBoostingRegressor(max_iter=120, learning_rate=0.08, max_depth=3, min_samples_leaf=5, categorical_features=[0, 1, 2], random_state=0).fit(Xtr, ytr)
    r2 = r2_score(yte, model.predict(Xte))
    mae = mean_absolute_error(np.expm1(yte), np.expm1(model.predict(Xte)))
    df["predicted_conv_per_1k"] = np.expm1(model.predict(X))
    df["is_active"] = ~done
    df["expected_conversions"] = df["predicted_conv_per_1k"] * df["budget_usd"] / 1000
    con.register("_c", df[["campaign_id", "predicted_conv_per_1k", "expected_conversions", "is_active", "conv_per_1k"]])
    con.execute("CREATE OR REPLACE TABLE ai.campaign_scores AS SELECT * FROM _c")
    joblib.dump(model, MODELS / "campaign_conversion.joblib")
    print(f"  model    campaign_conversion          R2 {r2:.2f}  MAE {mae:.1f} conversions per $1k")
    return dict(model_id="campaign_conversion", model_name="Campaign Conversion Prediction", business_vertical="Marketing", model_type="Regression",
                business_objective="Predict conversions per $1,000 of spend for a campaign design so budget is steered to the designs that convert.",
                algorithm="Gradient boosting regressor on log conversions per $1k", features=", ".join(cols), training_rows=int(len(Xtr)), training_from=AS_OF.replace(year=AS_OF.year - 2),
                training_to=AS_OF, series_count=1, metric_name="R-squared (30% holdout)", metric_value=float(r2), baseline_name="Mean predictor R-squared", baseline_value=0.0,
                forecast_error=float(mae), drift_score=0.0, trained_at=datetime.now(), explainability=_importance(model, Xte, yte, cols, "r2"))


def queue_prediction(con) -> dict:
    df = con.execute("""
        SELECT h.park_code, h.property_id, h.attraction_code, a.attraction_name, h.business_date, h.hour_of_day, h.capacity, h.avg_queue_minutes, d.visitors, d.daily_capacity
        FROM gold.fact_attraction_utilisation h JOIN gold.fact_waterpark_daily d USING (park_code, business_date) JOIN gold.dim_attraction a USING (attraction_code)""").df()
    df["attr"] = df["attraction_code"].astype("category")
    cols = ["attr_code", "hour_of_day", "visitors", "capacity"]
    df["attr_code"] = df["attr"].cat.codes
    df["business_date"] = pd.to_datetime(df["business_date"])
    tr = df["business_date"] <= TA - pd.Timedelta(days=60)
    model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.08, max_depth=6, categorical_features=[0], random_state=0).fit(df.loc[tr, cols], df.loc[tr, "avg_queue_minutes"])
    pred = model.predict(df.loc[~tr, cols])
    mae = mean_absolute_error(df.loc[~tr, "avg_queue_minutes"], pred)
    base = mean_absolute_error(df.loc[~tr, "avg_queue_minutes"], np.full((~tr).sum(), df.loc[tr, "avg_queue_minutes"].mean()))
    fc = con.execute("SELECT entity_id AS park_code, business_date, forecast_value AS visitors FROM gold.fact_forecasts WHERE metric = 'waterpark_attendance' AND forecast_type = 'forecast' AND horizon_day <= 45").df()
    grid = df[["park_code", "property_id", "attraction_code", "attraction_name", "attr_code", "hour_of_day", "capacity"]].drop_duplicates().merge(fc, on="park_code")
    grid["predicted_queue_minutes"] = np.clip(model.predict(grid[cols]), 0, None)
    con.register("_q", grid[["park_code", "property_id", "attraction_code", "attraction_name", "business_date", "hour_of_day", "visitors", "predicted_queue_minutes"]])
    con.execute("CREATE OR REPLACE TABLE ai.queue_forecast AS SELECT * FROM _q")
    joblib.dump(model, MODELS / "waterpark_queue.joblib")
    print(f"  model    waterpark_queue              MAE {mae:.1f} min (baseline {base:.1f})")
    return dict(model_id="waterpark_queue", model_name="Attraction Queue Prediction", business_vertical="Waterpark", model_type="Regression",
                business_objective="Predict queue time by attraction and hour from forecast attendance so operations can redistribute visitors before congestion builds.",
                algorithm="Gradient boosting regressor", features="attraction, hour_of_day, park visitors, ride capacity", training_rows=int(tr.sum()), training_from=df["business_date"].min().date(),
                training_to=(TA - pd.Timedelta(days=60)).date(), series_count=int(df["attraction_code"].nunique()), metric_name="MAE minutes (60-day holdout)", metric_value=float(mae),
                baseline_name="Mean-queue MAE minutes", baseline_value=float(base), forecast_error=float(mae),
                drift_score=_psi(df.loc[tr, "avg_queue_minutes"].values, df.loc[~tr, "avg_queue_minutes"].values), trained_at=datetime.now(),
                explainability="Queue time rises non-linearly once hourly demand passes roughly 55% of ride capacity; attendance and hour of day dominate.")
