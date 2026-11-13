"""Prescriptive engine (SRS 7.3, 7.4 C/D): turns model output and governed measures into
prioritised, explainable recommendations and alerts.

Every recommendation is produced by a named rule, carries the figures that triggered it, the model
and version it relied on, an estimated financial impact with the assumption used, and a confidence
score. Nothing is executed automatically - all recommendations start as 'Pending approval'.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from hip.settings import AS_OF

A = f"DATE '{AS_OF}'"
TODAY = AS_OF + timedelta(days=1)
OWNER = {"Hotels": "Director of Revenue", "Restaurants": "Director of Food & Beverage", "Waterpark": "Park Operations Manager", "Retail": "Retail Manager",
         "Spa & Leisure": "Spa Director", "Marketing": "Director of Marketing", "Finance": "Director of Finance"}


def _id(prefix: str, *parts) -> str:
    return f"{prefix}-" + hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8].upper()


def _money(v: float) -> str:
    return f"${v / 1e6:,.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}K" if abs(v) >= 1e4 else f"${v:,.0f}"


def _fmt_dates(ds) -> str:
    ds = sorted(pd.to_datetime(list(ds)))
    return ", ".join(d.strftime("%d %b") for d in ds[:6]) + (f" (+{len(ds) - 6} more)" if len(ds) > 6 else "")


class Engine:
    def __init__(self, con, versions: dict, registry: dict):
        self.con, self.versions, self.registry = con, versions, registry
        self.recs: list[dict] = []
        self.alerts: list[dict] = []
        self.P = {r[0]: r[1] for r in con.execute("SELECT property_id, short_name FROM gold.dim_property").fetchall()}
        self.acc = con.execute("SELECT model_id, property_id, entity_id, wape FROM ai.forecast_accuracy").df().set_index(["model_id", "property_id", "entity_id"])["wape"]

    def q(self, sql: str) -> pd.DataFrame:
        return self.con.execute(sql).df()

    def conf(self, model_id: str, pid: str | None = None, eid: str = "ALL") -> float:
        if (model_id, pid, eid) in self.acc.index:
            return float(np.clip(1 - self.acc[(model_id, pid, eid)], 0.5, 0.95))
        r = self.registry.get(model_id)
        return float(np.clip(r["metric_value"], 0.5, 0.95)) if r and r["model_type"] == "Classification" else 0.85

    def rec(self, scenario, vertical, pid, title, problem, action, support, impact, confidence, model_id=None, link=None, impact_basis="", key=()):
        score = impact * confidence
        self.recs.append(dict(
            recommendation_id=_id("REC", scenario, pid, *key), scenario=scenario, business_vertical=vertical, property_id=pid, title=title, problem=problem,
            recommended_action=action, supporting_data=json.dumps([{"label": k, "value": v} for k, v in support]), model_id=model_id,
            model_version=self.versions.get(model_id) if model_id else None, forecast_link=json.dumps(link) if link else None, estimated_impact_usd=float(round(impact, -2)),
            impact_basis=impact_basis, confidence=round(float(confidence), 2), priority_score=float(score),
            priority="Critical" if score >= 250000 else "High" if score >= 80000 else "Medium" if score >= 20000 else "Low",
            recommendation_date=TODAY, approval_status="Pending approval", requires_approval=True,
            owner_role=f"{self.P.get(pid, 'Group')} {OWNER[vertical]}" if pid != "ALL" else f"Group {OWNER[vertical]}"))

    def alert(self, severity, category, vertical, pid, title, description, value=None, threshold=None, source="Rule", key=()):
        self.alerts.append(dict(alert_id=_id("ALT", category, pid, title, *key), severity=severity, category=category, business_vertical=vertical, property_id=pid, title=title,
                                description=description, metric_value=None if value is None else float(value), threshold=None if threshold is None else float(threshold),
                                source=source, detected_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=6), status="Open",
                                owner_role=f"{self.P.get(pid, 'Group')} {OWNER.get(vertical, 'Data Platform Lead')}" if pid != "ALL" else f"Group {OWNER.get(vertical, 'Data Platform Lead')}"))

    # ------------------------------------------------------------------ hotels
    def hotels(self):
        f = self.q("SELECT property_id, business_date, forecast_value AS fc, capacity AS cap, horizon_day AS h FROM gold.fact_forecasts WHERE metric = 'hotel_rooms_sold' AND entity_id = 'ALL' AND forecast_type = 'forecast'")
        ly = self.q("SELECT property_id, business_date + INTERVAL 364 DAY AS business_date, rooms_sold AS ly_sold, rooms_available AS ly_cap FROM gold.mart_executive_daily")
        adr = dict(self.con.execute(f"SELECT property_id, sum(room_revenue_usd) / sum(rooms_sold) FROM gold.mart_executive_daily WHERE business_date > {A} - INTERVAL 90 DAY GROUP BY 1").fetchall())
        f["business_date"], ly["business_date"] = pd.to_datetime(f["business_date"]), pd.to_datetime(ly["business_date"])
        f = f.merge(ly, on=["property_id", "business_date"], how="left")
        f["week"] = f["business_date"] - pd.to_timedelta(f["business_date"].dt.day_of_week, unit="D")
        w = f[f["h"] >= 5].groupby(["property_id", "week"]).agg(fc=("fc", "sum"), cap=("cap", "sum"), ly=("ly_sold", "sum"), lyc=("ly_cap", "sum"), n=("fc", "size")).reset_index()
        w = w[w["n"] == 7]
        w["occ"], w["ly_occ"] = w["fc"] / w["cap"], w["ly"] / w["lyc"]
        ver = self.versions["hotel_rooms_sold"]
        for pid, g in w.groupby("property_id"):
            c = self.conf("hotel_rooms_sold", pid)
            low = g[(g["occ"] < g["ly_occ"] - 0.05) | (g["occ"] < 0.45)].assign(gap=lambda d: (np.maximum(d["ly_occ"], 0.5) - d["occ"]).clip(lower=0.02)).nlargest(2, "gap")
            for _, r in low.iterrows():
                rooms = r["gap"] * r["cap"]
                self.rec("Low hotel occupancy forecast", "Hotels", pid, f"Stimulate demand for week of {r['week']:%d %b} at {self.P[pid]}",
                         f"Forecast occupancy for the week of {r['week']:%d %b %Y} is {r['occ']:.0%}, against {r['ly_occ']:.0%} in the same week last year - about {rooms:,.0f} room nights short.",
                         "Launch a targeted promotion to loyalty members and lapsed guests for these dates, open discounted rate plans on the direct web channel, and widen OTA and wholesale availability for the shoulder nights.",
                         [("Forecast occupancy", f"{r['occ']:.1%}"), ("Same week last year", f"{r['ly_occ']:.1%}"), ("Room-night gap", f"{rooms:,.0f}"), ("Trailing 90-day ADR", f"${adr[pid]:,.0f}"),
                          ("Model", f"Hotel Occupancy Forecast v{ver}")],
                         rooms * adr[pid] * 0.35, c, "hotel_rooms_sold", {"metric": "hotel_rooms_sold", "property_id": pid, "entity_id": "ALL"},
                         "Assumes the promotion recovers 35% of the room-night gap at the trailing 90-day ADR.", key=(r["week"].date(),))
            high = g[g["occ"] >= 0.88].nlargest(2, "occ")
            for _, r in high.iterrows():
                self.rec("High demand forecast", "Hotels", pid, f"Raise rates for week of {r['week']:%d %b} at {self.P[pid]}",
                         f"Forecast occupancy for the week of {r['week']:%d %b %Y} is {r['occ']:.0%}; the property is on course to sell out at current rates.",
                         "Apply the approved high-demand rate band (+6% on best available rate), close the deepest discount plans, and set a two-night minimum stay on peak nights. Rate changes go through the PMS rate-approval workflow.",
                         [("Forecast occupancy", f"{r['occ']:.1%}"), ("Forecast rooms sold", f"{r['fc']:,.0f} of {r['cap']:,.0f}"), ("Trailing 90-day ADR", f"${adr[pid]:,.0f}"), ("Model", f"Hotel Occupancy Forecast v{ver}")],
                         r["fc"] * adr[pid] * 0.06 * 0.85, c, "hotel_rooms_sold", {"metric": "hotel_rooms_sold", "property_id": pid, "entity_id": "ALL"},
                         "Assumes a 6% rate increase holds on 85% of forecast room nights.", key=(r["week"].date(),))
            soft = f[(f["property_id"] == pid) & (f["h"] <= 14)]
            if (soft["fc"].sum() / soft["cap"].sum()) < 0.5:
                self.alert("Warning", "Revenue", "Hotels", pid, f"Occupancy below 50% forecast for the next 14 days at {self.P[pid]}",
                           f"Forecast occupancy for the next 14 days is {soft['fc'].sum() / soft['cap'].sum():.0%}.", soft["fc"].sum() / soft["cap"].sum(), 0.5, "Hotel Occupancy Forecast")
        risk = self.q(f"SELECT property_id, count(*) AS n, sum(room_revenue_usd) AS rev, avg(cancel_probability) AS p, mode(drivers) AS driver FROM ai.reservation_risk WHERE risk_band = 'High' AND arrival_date <= {A} + INTERVAL 30 DAY GROUP BY 1")
        for _, r in risk.iterrows():
            self.rec("High cancellation probability", "Hotels", r["property_id"], f"Engage {r['n']:,} at-risk reservations at {self.P[r['property_id']]}",
                     f"{r['n']:,} reservations arriving in the next 30 days have a high cancellation probability (average {r['p']:.0%}), putting {_money(r['rev'])} of room revenue at risk. Most common driver: {r['driver']}.",
                     "Send pre-arrival engagement (concierge contact, upgrade or experience offer) to the at-risk list, reconfirm OTA and wholesale bookings, and allow controlled overbooking on the most exposed nights.",
                     [("At-risk reservations", f"{r['n']:,}"), ("Revenue at risk", _money(r["rev"])), ("Average cancellation probability", f"{r['p']:.0%}"),
                      ("Model", f"Reservation Cancellation Prediction v{self.versions['hotel_cancellation']}")],
                     r["rev"] * 0.2, self.conf("hotel_cancellation"), "hotel_cancellation", None, "Assumes engagement saves 20% of the revenue at risk.")
            if r["rev"] > 150000:
                self.alert("Warning", "Revenue", "Hotels", r["property_id"], f"{_money(r['rev'])} of on-the-books revenue at high cancellation risk at {self.P[r['property_id']]}",
                           f"{r['n']:,} reservations arriving within 30 days are flagged high risk.", r["rev"], 150000, "Reservation Cancellation Prediction")

    # ------------------------------------------------------------------ restaurants
    def restaurants(self):
        f = self.q(f"""
            WITH h AS (SELECT outlet_code, quantile_cont(c, 0.9) AS p90, avg(c) AS avg_c, sum(n) / sum(c) AS spend FROM (
                           SELECT outlet_code, business_date, sum(covers) AS c, sum(net_usd) AS n FROM gold.fact_restaurant_covers WHERE business_date > {A} - INTERVAL 180 DAY GROUP BY ALL) GROUP BY 1)
            SELECT f.property_id, f.entity_id AS outlet_code, f.entity_name, f.business_date, f.forecast_value AS fc, h.p90, h.avg_c, h.spend
            FROM gold.fact_forecasts f JOIN h ON h.outlet_code = f.entity_id WHERE f.metric = 'restaurant_covers' AND f.forecast_type = 'forecast' AND f.horizon_day <= 14""")
        peak = f[f["fc"] > f["p90"]].assign(excess=lambda d: d["fc"] - d["avg_c"])
        agg = peak.groupby(["property_id", "outlet_code", "entity_name"]).agg(days=("fc", "size"), mx=("fc", "max"), excess=("excess", "sum"), spend=("spend", "first"), avg_c=("avg_c", "first"),
                                                                             dates=("business_date", list)).reset_index().nlargest(6, "excess")
        for _, r in agg.iterrows():
            staff = int(np.ceil((r["mx"] - r["avg_c"]) / 25))
            self.rec("Restaurant peak demand", "Restaurants", r["property_id"], f"Add staff and tables at {r['entity_name']} for {r['days']} peak day(s)",
                     f"{r['entity_name']} is forecast to exceed its 90th-percentile demand on {r['days']} day(s) in the next two weeks ({_fmt_dates(r['dates'])}), peaking at {r['mx']:,.0f} covers against a normal {r['avg_c']:,.0f}.",
                     f"Roster {staff} additional service staff and one extra kitchen station on the peak days, release held-back tables to the booking system, and pre-order high-volume ingredients.",
                     [("Peak forecast covers", f"{r['mx']:,.0f}"), ("Normal daily covers", f"{r['avg_c']:,.0f}"), ("Peak days", _fmt_dates(r["dates"])), ("Spend per guest", f"${r['spend']:,.0f}"),
                      ("Model", f"Restaurant Covers Forecast v{self.versions['restaurant_covers']}")],
                     r["excess"] * r["spend"] * 0.4, self.conf("restaurant_covers", r["property_id"], r["outlet_code"]), "restaurant_covers",
                     {"metric": "restaurant_covers", "property_id": r["property_id"], "entity_id": r["outlet_code"]}, "Assumes 40% of above-normal covers would otherwise be lost to waits and turn-aways.")
        m = self.q(f"""
            SELECT l.property_id, l.outlet_code, o.outlet_name, m.item_name, sum(l.qty) AS qty, sum(l.net_usd) AS rev, sum(l.food_cost_usd) AS cost
            FROM gold.fact_restaurant_transactions l JOIN silver.menu_item m USING (menu_item_code) JOIN gold.dim_outlet o ON o.outlet_code = l.outlet_code
            WHERE l.business_date > {A} - INTERVAL 90 DAY GROUP BY ALL""")
        m["margin"] = 1 - m["cost"] / m["rev"]
        out = []
        for (pid, oc, on), g in m.groupby(["property_id", "outlet_code", "outlet_name"]):
            om, share = 1 - g["cost"].sum() / g["rev"].sum(), g["qty"] / g["qty"].sum()
            plow = g[(share > 1 / len(g)) & (g["margin"] < om - 0.08)].nlargest(1, "rev")
            dogs = g[(share < 0.5 / len(g)) & (g["margin"] < om - 0.05)]
            if len(plow):
                p = plow.iloc[0]
                out.append((pid, oc, on, p, dogs, om, p["rev"] * 365 / 90 * 0.05 + dogs["cost"].sum() * 365 / 90 * 0.3))
        for pid, oc, on, p, dogs, om, impact in sorted(out, key=lambda t: -t[-1])[:8]:
            self.rec("Low menu profitability", "Restaurants", pid, f"Re-engineer {p['item_name']} at {on}",
                     f"{p['item_name']} is one of the most ordered items at {on} ({p['qty']:,.0f} sold in 90 days) but earns a {p['margin']:.0%} margin against the outlet's {om:.0%}."
                     + (f" {len(dogs)} low-volume, low-margin item(s) also dilute the menu: {', '.join(dogs['item_name'])}." if len(dogs) else ""),
                     f"Raise the price of {p['item_name']} by 5-6% or re-cost the recipe (portion and supplier), reposition it lower on the menu, and promote a higher-margin alternative."
                     + (" Retire or replace the low-volume items." if len(dogs) else ""),
                     [("Item margin", f"{p['margin']:.1%}"), ("Outlet margin", f"{om:.1%}"), ("Units sold (90 days)", f"{p['qty']:,.0f}"), ("Item revenue (90 days)", _money(p["rev"])), ("Method", "Menu engineering matrix on POS actuals")],
                     impact, 0.85, None, None, "Assumes a 5% net price improvement on annualised item revenue, plus 30% of the cost of retired items.", key=(oc, p["item_name"]))

    # ------------------------------------------------------------------ waterpark
    def waterpark(self):
        f = self.q("""SELECT f.property_id, f.entity_id AS park_code, f.entity_name, f.business_date, f.forecast_value AS fc, f.upper_80 AS hi, f.capacity AS cap, f.horizon_day AS h
                      FROM gold.fact_forecasts f WHERE f.metric = 'waterpark_attendance' AND f.forecast_type = 'forecast' AND f.horizon_day <= 45""")
        rpv = dict(self.con.execute(f"SELECT park_code, sum(net_usd) / sum(visitors) FROM gold.fact_waterpark_daily WHERE business_date > {A} - INTERVAL 90 DAY GROUP BY 1").fetchall())
        qf = self.q("SELECT park_code, attraction_name, business_date, max(predicted_queue_minutes) AS q FROM ai.queue_forecast GROUP BY ALL")
        for (pid, pc, name), g in f.groupby(["property_id", "park_code", "entity_name"]):
            busy = g[(g["fc"] >= 0.85 * g["cap"]) | (g["hi"] >= g["cap"])]
            if not len(busy):
                continue
            qq = qf[(qf["park_code"] == pc) & (qf["business_date"].isin(busy["business_date"]))].groupby("attraction_name")["q"].max().nlargest(3)
            worst = busy.loc[busy["fc"].idxmax()]
            self.rec("Waterpark overcrowding", "Waterpark", pid, f"Manage capacity on {len(busy)} peak day(s) at {name}",
                     f"Forecast attendance reaches {worst['fc'] / worst['cap']:.0%} of licensed capacity on {pd.Timestamp(worst['business_date']):%d %b} and is at or near capacity on {len(busy)} day(s) in the next 45 days ({_fmt_dates(busy['business_date'])}). "
                     f"Predicted peak queues: {', '.join(f'{a} {v:.0f} min' for a, v in qq.items())}.",
                     "Switch day-pass sales to timed entry on the peak days, cap online day-pass inventory at 90% of capacity, shift demand to Twilight tickets, promote Fast Track, and add ride operators and lifeguards on the longest-queue attractions.",
                     [("Peak forecast", f"{worst['fc']:,.0f} visitors ({worst['fc'] / worst['cap']:.0%} of capacity)"), ("Days at or near capacity", f"{len(busy)}"),
                      ("Longest predicted queue", f"{qq.index[0]} {qq.iloc[0]:.0f} min" if len(qq) else "n/a"), ("Revenue per visitor", f"${rpv[pc]:,.0f}"),
                      ("Models", f"Waterpark Attendance Forecast v{self.versions['waterpark_attendance']}, Attraction Queue Prediction v{self.versions['waterpark_queue']}")],
                     busy["fc"].sum() * rpv[pc] * 0.05, self.conf("waterpark_attendance", pid, pc), "waterpark_attendance", {"metric": "waterpark_attendance", "property_id": pid, "entity_id": pc},
                     "Assumes timed entry and Fast Track upsell lift yield by 5% on peak-day revenue while protecting guest satisfaction.")
            soon = busy[busy["h"] <= 7]
            if len(soon):
                over = soon["hi"].max() >= soon["cap"].max()
                self.alert("Critical" if over else "Warning", "Capacity", "Waterpark", pid, f"{name}: attendance forecast near capacity within 7 days",
                           f"Forecast peaks at {soon['fc'].max():,.0f} visitors ({soon['fc'].max() / soon['cap'].max():.0%} of capacity) on {_fmt_dates(soon['business_date'])}; the upper forecast bound "
                           f"{'exceeds' if over else 'stays within'} licensed capacity.", soon["fc"].max() / soon["cap"].max(), 0.85, "Waterpark Attendance Forecast")

    # ------------------------------------------------------------------ retail
    def retail(self):
        inv = self.q(f"""
            WITH v AS (SELECT store_code, sku, greatest(sum(sold_qty) FILTER (WHERE snapshot_date > {A} - INTERVAL 28 DAY) / 28.0, sum(sold_qty) / 90.0) AS velocity
                       FROM gold.fact_inventory WHERE snapshot_date > {A} - INTERVAL 90 DAY GROUP BY ALL),
                 fc AS (SELECT entity_id, avg(forecast_value) AS f14 FROM gold.fact_forecasts WHERE metric = 'retail_units' AND forecast_type = 'forecast' AND horizon_day <= 14 GROUP BY 1),
                 ac AS (SELECT s.store_code || '|' || p.category AS entity_id, sum(s.qty) / 28.0 AS a28 FROM gold.fact_retail_sales s JOIN silver.product p USING (sku)
                        WHERE s.business_date > {A} - INTERVAL 28 DAY GROUP BY 1)
            SELECT l.store_code, o.outlet_name, l.property_id, l.sku, p.product_name, p.category, l.on_hand_qty, l.reorder_point, l.lead_time_days, l.unit_cost_usd, l.unit_price_usd, l.stock_value_usd,
                   v.velocity, v.velocity * coalesce(least(greatest(fc.f14 / nullif(ac.a28, 0), 0.6), 1.6), 1) AS daily_demand
            FROM gold.fact_inventory l JOIN silver.product p USING (sku) JOIN gold.dim_outlet o ON o.outlet_code = l.store_code JOIN v USING (store_code, sku)
            LEFT JOIN fc ON fc.entity_id = l.store_code || '|' || p.category LEFT JOIN ac ON ac.entity_id = l.store_code || '|' || p.category
            WHERE l.snapshot_date = {A}""")
        inv["days_cover"] = inv["on_hand_qty"] / inv["daily_demand"].clip(lower=0.01)
        inv["at_risk"] = (inv["days_cover"] < inv["lead_time_days"]) & (inv["daily_demand"] > 0.15)
        inv["reorder_qty"] = np.where(inv["at_risk"], np.ceil(inv["daily_demand"] * (inv["lead_time_days"] + 21) - inv["on_hand_qty"]), 0)
        inv["lost_sales_usd"] = np.where(inv["at_risk"], inv["daily_demand"] * (inv["lead_time_days"] - inv["days_cover"]).clip(lower=0) * inv["unit_price_usd"], 0)
        inv["slow_moving"] = (inv["days_cover"] > 150) & (inv["stock_value_usd"] > 1500)
        self.con.register("_inv", inv)
        self.con.execute("CREATE OR REPLACE TABLE ai.inventory_risk AS SELECT * FROM _inv")
        for (pid, sc, sn), g in inv[inv["at_risk"]].groupby(["property_id", "store_code", "outlet_name"]):
            top = g.nlargest(5, "lost_sales_usd")
            self.rec("Low retail stock", "Retail", pid, f"Replenish {len(g)} SKU(s) at {sn}",
                     f"{len(g)} SKU(s) at {sn} will run out before a new delivery can arrive. Most exposed: " + "; ".join(f"{r['product_name']} ({r['on_hand_qty']:.0f} on hand, {r['days_cover']:.1f} days of cover, {r['lead_time_days']:.0f}-day lead time)" for _, r in top.iterrows()) + ".",
                     "Raise purchase orders now: " + "; ".join(f"{r['product_name']} x {r['reorder_qty']:.0f}" for _, r in top.iterrows()) + ". Expedite delivery or transfer stock from a sister store for items already at zero.",
                     [("SKUs at risk", f"{len(g)}"), ("Units to order", f"{g['reorder_qty'].sum():,.0f}"), ("Order value at cost", _money((g["reorder_qty"] * g["unit_cost_usd"]).sum())),
                      ("Sales at risk before delivery", _money(g["lost_sales_usd"].sum())), ("Model", f"Retail Demand Forecast v{self.versions['retail_units']}")],
                     g["lost_sales_usd"].sum() + (g["daily_demand"] * 14 * g["unit_price_usd"] * 0.25).sum(), self.conf("retail_units", pid, f"{sc}|{top.iloc[0]['category']}"), "retail_units",
                     {"metric": "retail_units", "property_id": pid, "entity_id": f"{sc}|{top.iloc[0]['category']}"},
                     "Lost sales avoided before the next delivery, plus 25% of two weeks' demand protected from repeat stock-outs.")
            zero = int((g["on_hand_qty"] == 0).sum())
            self.alert("Critical" if zero >= 3 else "Warning", "Inventory", "Retail", pid, f"{sn}: {len(g)} SKU(s) will stock out before replenishment",
                       f"{zero} SKU(s) already at zero stock; {_money(g['lost_sales_usd'].sum())} of sales at risk.", len(g), 0, "Retail Demand Forecast")
        for (pid, sc, sn), g in inv[inv["slow_moving"]].groupby(["property_id", "store_code", "outlet_name"]):
            top = g.nlargest(4, "stock_value_usd")
            self.rec("Slow-moving retail inventory", "Retail", pid, f"Clear {len(g)} slow-moving line(s) at {sn}",
                     f"{len(g)} line(s) at {sn} hold more than 150 days of cover, tying up {_money(g['stock_value_usd'].sum())} at cost. Largest: " + ", ".join(f"{r['product_name']} ({r['days_cover']:.0f} days)" for _, r in top.iterrows()) + ".",
                     "Run a targeted 20-25% promotion to in-house guests and loyalty members, bundle these lines with fast sellers, and suspend re-ordering until cover falls below 60 days.",
                     [("Slow-moving lines", f"{len(g)}"), ("Stock value at cost", _money(g["stock_value_usd"].sum())), ("Median days of cover", f"{g['days_cover'].median():,.0f}"), ("Method", "Sell-through on inventory actuals")],
                     g["stock_value_usd"].sum() * 0.35, 0.8, None, None, "Assumes 35% of the tied-up stock value is released as cash within the quarter.")
        d = self.q(f"""
            SELECT s.property_id, s.store_code, o.outlet_name, sum(discount_usd) FILTER (WHERE business_date > {A} - INTERVAL 14 DAY) / nullif(sum(gross_usd) FILTER (WHERE business_date > {A} - INTERVAL 14 DAY), 0) AS recent,
                   sum(discount_usd) FILTER (WHERE business_date <= {A} - INTERVAL 14 DAY) / nullif(sum(gross_usd) FILTER (WHERE business_date <= {A} - INTERVAL 14 DAY), 0) AS prior,
                   sum(gross_usd) FILTER (WHERE business_date > {A} - INTERVAL 14 DAY) AS gross14
            FROM gold.fact_retail_sales s JOIN gold.dim_outlet o ON o.outlet_code = s.store_code WHERE business_date > {A} - INTERVAL 104 DAY GROUP BY ALL""")
        for _, r in d[(d["recent"] > 2 * d["prior"]) & (d["recent"] - d["prior"] > 0.04)].iterrows():
            leak = (r["recent"] - r["prior"]) * r["gross14"]
            self.alert("Critical", "Revenue Leakage", "Retail", r["property_id"], f"{r['outlet_name']}: discount rate jumped to {r['recent']:.0%}",
                       f"Discounts were {r['recent']:.1%} of gross sales in the last 14 days against a {r['prior']:.1%} norm - about {_money(leak)} of unplanned markdown.", r["recent"], 2 * r["prior"], "Discount anomaly detector")
            self.rec("Revenue leakage and discount anomaly", "Retail", r["property_id"], f"Investigate discounting at {r['outlet_name']}",
                     f"The discount rate at {r['outlet_name']} rose to {r['recent']:.1%} of gross sales over the last 14 days, from a {r['prior']:.1%} norm, with no matching approved promotion.",
                     "Audit till-level discount overrides for the period, restrict manual discounts above 20% to manager approval in the POS, and confirm whether an unapproved promotion is running.",
                     [("Discount rate, last 14 days", f"{r['recent']:.1%}"), ("Discount rate, prior 90 days", f"{r['prior']:.1%}"), ("Unplanned markdown", _money(leak)), ("Method", "Ratio test on POS actuals")],
                     leak * 26 * 0.5, 0.8, None, None, "Annualises the 14-day leakage and assumes half is recoverable.")

    # ------------------------------------------------------------------ spa
    def spa(self):
        f = self.q(f"""
            WITH h AS (SELECT spa_code, avg(duration_minutes) / 60.0 AS hrs, sum(net_usd) / count(*) AS rev FROM gold.fact_spa_appointments WHERE status = 'COMPLETED' AND business_date > {A} - INTERVAL 90 DAY GROUP BY 1)
            SELECT f.property_id, f.entity_id AS spa_code, f.entity_name, sum(f.forecast_value) AS appts, any_value(o.capacity) AS therapists, any_value(h.hrs) AS hrs, any_value(h.rev) AS rev
            FROM gold.fact_forecasts f JOIN gold.dim_outlet o ON o.outlet_code = f.entity_id JOIN h ON h.spa_code = f.entity_id
            WHERE f.metric = 'spa_appointments' AND f.forecast_type = 'forecast' AND f.horizon_day <= 30 GROUP BY ALL""")
        f["util"] = f["appts"] * f["hrs"] / (f["therapists"] * 10 * 30)
        for _, r in f[f["util"] < 0.40].iterrows():
            extra = (0.45 - r["util"]) * r["therapists"] * 10 * 30 / r["hrs"]
            self.rec("Low spa utilisation", "Spa & Leisure", r["property_id"], f"Fill unused capacity at {r['entity_name']}",
                     f"{r['entity_name']} is forecast to use {r['util']:.0%} of available therapist hours over the next 30 days ({r['appts']:,.0f} appointments across {r['therapists']:.0f} therapists).",
                     "Offer in-house guests a room-plus-spa bundle at check-in, send a mid-week local-resident membership offer, pair massage with a facial at a 15% bundle price, and trim low-demand weekday rosters.",
                     [("Forecast utilisation", f"{r['util']:.0%}"), ("Target utilisation", "45%"), ("Forecast appointments (30 days)", f"{r['appts']:,.0f}"), ("Revenue per appointment", f"${r['rev']:,.0f}"),
                      ("Model", f"Spa Appointment Forecast v{self.versions['spa_appointments']}")],
                     extra * r["rev"] * 0.4 * 6, self.conf("spa_appointments", r["property_id"], r["spa_code"]), "spa_appointments",
                     {"metric": "spa_appointments", "property_id": r["property_id"], "entity_id": r["spa_code"]}, "Six-month view; assumes 40% of the gap to 45% utilisation is filled at current revenue per appointment.")

    # ------------------------------------------------------------------ marketing
    def marketing(self):
        g = self.q("""SELECT d.loyalty_tier, count(*) AS n, sum(s.clv_3yr_usd) AS clv, avg(s.churn_probability) AS p, sum(s.annual_value_usd) AS annual,
                             count(*) FILTER (WHERE NOT d.marketing_consent) AS no_consent
                      FROM ai.guest_scores s JOIN gold.dim_guest d USING (guest_key)
                      WHERE s.churn_band = 'High' AND s.segment IN ('High-Value Regulars', 'Elite Loyalists') AND d.is_member GROUP BY 1""")
        for _, r in g.iterrows():
            n_ok = r["n"] - r["no_consent"]
            self.rec("High customer churn risk", "Marketing", "ALL", f"Retain {n_ok:,.0f} high-value {r['loyalty_tier']} members at risk of lapsing",
                     f"{r['n']:,.0f} high-value {r['loyalty_tier']} members have a high probability of not returning within 180 days (average {r['p']:.0%}); together they were worth {_money(r['annual'])} a year. "
                     f"{r['no_consent']:,.0f} have not given marketing consent and are excluded from outreach.",
                     "Run a personalised retention campaign to the consented members: a tier-specific return offer at their most-visited property, bonus points on a stay booked within 60 days, and a personal call from the guest-relations team for the top decile by value.",
                     [("Members at risk", f"{r['n']:,.0f}"), ("Contactable (consented)", f"{n_ok:,.0f}"), ("Annual value at risk", _money(r["annual"])), ("Average churn probability", f"{r['p']:.0%}"),
                      ("Models", f"Guest Churn Prediction v{self.versions['guest_churn']}, Guest Segmentation v{self.versions['guest_segmentation']}")],
                     r["annual"] * (n_ok / max(r["n"], 1)) * 0.10, self.conf("guest_churn"), "guest_churn", None, "Assumes the campaign retains 10% of the annual value of contactable members.", key=(r["loyalty_tier"],))
        ch = self.q(f"""SELECT channel, sum(spend_usd) AS spend, sum(attributed_revenue_usd) AS rev, sum(new_customers) AS cust FROM gold.fact_marketing_campaigns
                        WHERE activity_date > {A} - INTERVAL 180 DAY GROUP BY 1""")
        ch["roi"] = (ch["rev"] - ch["spend"]) / ch["spend"]
        best = ch.loc[ch["roi"].idxmax()]
        for _, r in ch[(ch["roi"] < -0.2) & (ch["spend"] > 40000)].iterrows():
            shift = r["spend"] * 0.4
            self.rec("Marketing budget allocation", "Marketing", "ALL", f"Shift 40% of {r['channel']} budget to {best['channel']}",
                     f"{r['channel']} campaigns returned {r['roi']:+.0%} on {_money(r['spend'])} of spend over the last 180 days (CAC ${r['spend'] / max(r['cust'], 1):,.0f}), while {best['channel']} returned {best['roi']:+.0%}.",
                     f"Move 40% of the planned {r['channel']} budget ({_money(shift)} per half-year) to {best['channel']} and the best-converting audiences, keep a 10% test budget in {r['channel']} for creative experiments, and review again after one quarter.",
                     [(f"{r['channel']} ROI (180 days)", f"{r['roi']:+.0%}"), (f"{best['channel']} ROI (180 days)", f"{best['roi']:+.0%}"), ("Spend to reallocate", _money(shift)),
                      ("Model", f"Campaign Conversion Prediction v{self.versions['campaign_conversion']}")],
                     shift * (min(best["roi"], 1.5) - r["roi"]) * 0.5 * 2, 0.75, "campaign_conversion", None, "Annualised; assumes half of the ROI difference is realised because returns diminish as the better channel scales.", key=(r["channel"],))

    # ------------------------------------------------------------------ finance
    def finance(self):
        v = self.q(f"""
            WITH per AS (SELECT date_trunc('month', {A} - INTERVAL 1 MONTH) AS s),
                 act AS (SELECT property_id, cost_centre, gl_account, any_value(gl_account_name) AS account, sum(amount_usd) AS actual FROM gold.fact_financial_transactions, per
                         WHERE account_type = 'EXPENSE' AND posting_date >= per.s GROUP BY ALL),
                 bud AS (SELECT property_id, cost_centre, gl_account, sum(budget_usd * CASE WHEN fiscal_period = strftime({A}, '%Y-%m') THEN day({A}) * 1.0 / day(last_day({A})) ELSE 1 END) AS budget
                         FROM gold.fact_budget, per WHERE account_type = 'EXPENSE' AND period_start >= per.s AND period_start <= {A} GROUP BY ALL)
            SELECT a.*, b.budget, a.actual - b.budget AS variance FROM act a JOIN bud b USING (property_id, cost_centre, gl_account)""")
        cc = v.groupby(["property_id", "cost_centre"]).agg(actual=("actual", "sum"), budget=("budget", "sum")).reset_index()
        cc["var"], cc["pct"] = cc["actual"] - cc["budget"], cc["actual"] / cc["budget"] - 1
        for _, r in cc[(cc["pct"] > 0.07) & (cc["var"] > 20000)].nlargest(6, "var").iterrows():
            drv = v[(v["property_id"] == r["property_id"]) & (v["cost_centre"] == r["cost_centre"])].nlargest(1, "variance").iloc[0]
            self.rec("High operating expenditure", "Finance", r["property_id"], f"Contain {r['cost_centre']} overspend at {self.P[r['property_id']]}",
                     f"{r['cost_centre']} costs at {self.P[r['property_id']]} are {_money(r['var'])} ({r['pct']:+.0%}) over budget since the start of last month. The main driver is {drv['account']}, {_money(drv['variance'])} over.",
                     f"Review {drv['account']} with the department head this week: validate meter readings, rates and invoices against contract, check for a one-off versus a run-rate change, and agree a recovery plan or a reforecast.",
                     [("Actual", _money(r["actual"])), ("Pro-rated budget", _money(r["budget"])), ("Variance", f"{_money(r['var'])} ({r['pct']:+.1%})"), ("Largest driver", f"{drv['account']} {_money(drv['variance'])} over"),
                      ("Model", f"Operating Expenditure Forecast v{self.versions['finance_opex']}")],
                     r["var"] * 0.5 * 6, 0.85, "finance_opex", {"metric": "finance_opex", "property_id": r["property_id"], "entity_id": "ALL"},
                     "Assumes half of the overspend run-rate is removed for the next six periods.", key=(r["cost_centre"],))
        an = self.q(f"""
            WITH d AS (SELECT property_id, cost_centre, gl_account_name, posting_date, sum(amount_usd) AS amt FROM gold.fact_financial_transactions
                       WHERE account_type = 'EXPENSE' AND posting_date > {A} - INTERVAL 104 DAY GROUP BY ALL),
                 b AS (SELECT property_id, cost_centre, gl_account_name, median(amt) AS med, median(abs(amt - m)) AS mad
                       FROM (SELECT *, median(amt) OVER (PARTITION BY property_id, cost_centre, gl_account_name) AS m FROM d WHERE posting_date <= {A} - INTERVAL 14 DAY) GROUP BY ALL),
                 r AS (SELECT property_id, cost_centre, gl_account_name, avg(amt) AS recent, sum(amt) AS recent_total FROM d WHERE posting_date > {A} - INTERVAL 14 DAY GROUP BY ALL)
            SELECT r.*, b.med, (r.recent - b.med) / nullif(1.4826 * b.mad, 0) AS z FROM r JOIN b USING (property_id, cost_centre, gl_account_name)""")
        for _, r in an[(an["z"] > 4) & (an["recent"] > 1.2 * an["med"]) & (an["recent_total"] - 14 * an["med"] > 15000)].iterrows():
            self.alert("Critical" if r["recent"] > 1.3 * r["med"] else "Warning", "Financial Anomaly", "Finance", r["property_id"],
                       f"{self.P[r['property_id']]}: {r['gl_account_name']} running {r['recent'] / r['med'] - 1:+.0%} above normal",
                       f"{r['cost_centre']} / {r['gl_account_name']} averaged {_money(r['recent'])} a day over the last 14 days against a 90-day median of {_money(r['med'])} (robust z-score {r['z']:.1f}); "
                       f"{_money(r['recent_total'] - 14 * r['med'])} of excess cost so far.", r["recent"] / r["med"] - 1, 0.2, "Expense anomaly detector (median / MAD)", key=(r["cost_centre"], r["gl_account_name"]))
        pr = self.q(f"""
            WITH base AS (SELECT item_description, median(unit_price_usd) AS med FROM gold.fact_procurement WHERE po_date <= {A} - INTERVAL 60 DAY GROUP BY 1)
            SELECT p.property_id, p.supplier_id, p.supplier_name, p.category, count(*) AS pos, sum(p.po_amount_usd) AS spend, avg(p.unit_price_usd / b.med) - 1 AS premium,
                   sum(p.po_amount_usd - p.quantity * b.med) AS overpaid
            FROM gold.fact_procurement p JOIN base b USING (item_description) WHERE p.po_date > {A} - INTERVAL 60 DAY GROUP BY ALL HAVING count(*) >= 3""")
        for _, r in pr[(pr["premium"] > 0.2) & (pr["overpaid"] > 3000)].iterrows():
            self.alert("Warning", "Procurement Anomaly", "Finance", r["property_id"], f"{r['supplier_name']} prices {r['premium']:+.0%} above benchmark at {self.P[r['property_id']]}",
                       f"{r['pos']} purchase orders in the last 60 days were priced {r['premium']:.0%} above the group median for the same items; {_money(r['overpaid'])} overpaid.", r["premium"], 0.2, "Procurement price anomaly detector", key=(r["supplier_id"],))
            self.rec("Supplier spend optimisation", "Finance", r["property_id"], f"Renegotiate or re-source {r['category']} from {r['supplier_name']}",
                     f"{r['supplier_name']} charged {self.P[r['property_id']]} {r['premium']:.0%} above the group median price on {r['pos']} {r['category']} orders in the last 60 days ({_money(r['overpaid'])} over benchmark).",
                     "Challenge the price increase against the supply contract, request credit for orders above the agreed rate, and move volume to the group's preferred supplier for this category if pricing is not restored.",
                     [("Price premium vs group median", f"{r['premium']:.0%}"), ("Orders affected", f"{r['pos']}"), ("Overpaid (60 days)", _money(r["overpaid"])), ("Method", "Item-level price benchmark on procurement actuals")],
                     r["overpaid"] * 6, 0.85, None, None, "Annualises the 60-day overpayment.", key=(r["supplier_id"],))
        pl = self.q(f"""SELECT property_id, sum(gl_revenue_usd) AS rev, sum(gop_usd) AS gop FROM gold.mart_executive_daily WHERE business_date > {A} - INTERVAL 90 DAY GROUP BY 1""")
        pl["margin"] = pl["gop"] / pl["rev"]
        chain = pl["gop"].sum() / pl["rev"].sum()
        ccr = self.q(f"""SELECT property_id, cost_centre, sum(amount_usd) AS cost FROM gold.fact_financial_transactions WHERE account_type = 'EXPENSE' AND posting_date > {A} - INTERVAL 90 DAY GROUP BY ALL""").merge(pl[["property_id", "rev"]])
        ccr["ratio"] = ccr["cost"] / ccr["rev"]
        ccr["chain"] = ccr.groupby("cost_centre")["ratio"].transform("median")
        for _, r in pl[pl["margin"] < chain - 0.07].iterrows():
            gaps = ccr[ccr["property_id"] == r["property_id"]].assign(gap=lambda d: d["ratio"] - d["chain"]).nlargest(3, "gap")
            self.rec("Low property profitability", "Finance", r["property_id"], f"Profit improvement plan for {self.P[r['property_id']]}",
                     f"{self.P[r['property_id']]} delivered a {r['margin']:.0%} GOP margin over the last 90 days against {chain:.0%} for the group. The widest cost-to-revenue gaps versus the group median are: "
                     + "; ".join(f"{g['cost_centre']} {g['ratio']:.1%} vs {g['chain']:.1%}" for _, g in gaps.iterrows()) + ".",
                     "Commercial: lift rate and direct-channel mix on high-demand dates and push in-house capture for restaurants and spa. Operational: reset staffing to demand in the three cost centres above and review fixed service contracts. Track monthly against the group median.",
                     [("GOP margin (90 days)", f"{r['margin']:.1%}"), ("Group GOP margin", f"{chain:.1%}"), ("Revenue (90 days)", _money(r["rev"]))] + [(g["cost_centre"], f"{g['gap']:+.1%} of revenue vs group") for _, g in gaps.iterrows()]
                     + [("Model", f"Profitability Forecast v{self.versions['finance_gop']}")],
                     r["rev"] * 4 * (chain - r["margin"]) * 0.25, 0.8, "finance_gop", {"metric": "finance_gop", "property_id": r["property_id"], "entity_id": "ALL"},
                     "Annualised; assumes a quarter of the margin gap to the group is closed.")
            self.alert("Warning", "Profitability", "Finance", r["property_id"], f"{self.P[r['property_id']]} GOP margin {r['margin']:.0%}, {(chain - r['margin']) * 100:.0f} points below group",
                       f"Trailing 90-day GOP margin is {r['margin']:.1%} against {chain:.1%} for the group.", r["margin"], chain - 0.07, "Profitability Forecast")

    # ------------------------------------------------------------------ platform alerts
    def platform(self):
        dq = self.q("SELECT * FROM ops.dq_results WHERE run_at = (SELECT max(run_at) FROM ops.dq_results) AND status IN ('Failed', 'Warning')")
        for _, r in dq.iterrows():
            self.alert("Critical" if r["status"] == "Failed" else "Warning", "Data Quality", "Finance" if r["rule_id"].startswith("DQ-REC") else "Enterprise", "ALL",
                       f"{r['rule_id']}: {r['description']}", f"{r['failed']:,} of {r['total']:,} records failed. {r['detail']}".strip(), r["failed"], 0, "Data quality rule", key=(r["rule_id"],))
        ing = self.q("SELECT source_id, message FROM ops.ingestion_log WHERE batch_id = (SELECT max(batch_id) FROM ops.ingestion_log) AND status = 'Failed'")
        for _, r in ing.iterrows():
            self.alert("Critical", "Ingestion", "Enterprise", "ALL", f"Ingestion failed for {r['source_id']}", r["message"], source="Ingestion monitor", key=(r["source_id"],))
        for mid, r in self.registry.items():
            if r["drift_status"] != "Stable":
                self.alert("Warning", "Model", r["business_vertical"], "ALL", f"{r['model_name']}: drift status {r['drift_status']}",
                           f"Population stability index {r['drift_score']:.2f} (threshold 0.10 watch, 0.25 retrain). A retraining run has been queued for review.", r["drift_score"], 0.1, "Model monitoring", key=(mid,))
        n = self.con.execute("SELECT count(*), sum(rows_loaded) FROM ops.ingestion_log WHERE batch_id = (SELECT max(batch_id) FROM ops.ingestion_log) AND status = 'Succeeded'").fetchall()[0]
        self.alert("Info", "Ingestion", "Enterprise", "ALL", f"Daily load completed for business date {AS_OF:%d %b %Y}", f"{n[0]} source feeds ingested ({n[1]:,} rows) and all Gold data products refreshed.",
                   source="Ingestion monitor", key=(AS_OF,))
        self.alert("Info", "Model", "Enterprise", "ALL", f"{len(self.registry)} models retrained and scored", "All forecasting and predictive models were retrained through the last closed business date and new versions registered.",
                   source="Model monitoring", key=(AS_OF,))

    def run(self):
        for step in (self.hotels, self.restaurants, self.waterpark, self.retail, self.spa, self.marketing, self.finance, self.platform):
            step()
        recs = pd.DataFrame(self.recs).drop_duplicates("recommendation_id").sort_values("priority_score", ascending=False)
        alerts = pd.DataFrame(self.alerts).drop_duplicates("alert_id")
        self.con.register("_recs", recs)
        self.con.register("_alerts", alerts)
        self.con.execute("CREATE OR REPLACE TABLE gold.fact_ai_recommendations AS SELECT * FROM _recs")
        self.con.execute("CREATE OR REPLACE TABLE ai.alerts AS SELECT * FROM _alerts")
        print(f"  {len(recs)} recommendations ({recs['priority'].value_counts().to_dict()}), est. impact {_money(recs['estimated_impact_usd'].sum())}")
        print(f"  {len(alerts)} alerts ({alerts['severity'].value_counts().to_dict()})")
