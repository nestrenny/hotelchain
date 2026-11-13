"""Enterprise dashboards (SRS 6). Each function returns a declarative page spec - KPI tiles, chart
and table panels, and insights - computed from the Gold layer for the persona's properties and the
selected period. The web client renders any spec with one generic renderer, which is what keeps the
design language and KPI definitions consistent across all eight dashboards (SRS 6.9).

In the Azure target these specs correspond to Power BI report pages over a Fabric semantic model.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from hip.settings import AS_OF

OCC = ("CHECKED_OUT", "CHECKED_IN")


class Ctx:
    def __init__(self, con, scope, props: list[str], start: date, end: date, compare: str):
        self.con, self.scope, self.props, self.start, self.end = con, scope, props, start, end
        self.days = (end - start).days + 1
        if compare == "ly":
            self.cs, self.ce, self.clabel = start - timedelta(days=364), end - timedelta(days=364), "same period last year"
        else:
            self.cs, self.ce, self.clabel = start - timedelta(days=self.days), start - timedelta(days=1), "previous period"
        self.grain = "day" if self.days <= 93 else "week" if self.days <= 400 else "month"
        self.live = end >= AS_OF
        self.defs = {r[0]: r[1] for r in con.execute("SELECT kpi, definition || ' ' || exclusions FROM ops.kpi_glossary").fetchall()}
        self.names = dict(con.execute("SELECT property_id, short_name FROM gold.dim_property").fetchall())

    def P(self, col: str = "property_id") -> str:
        return f"{col} IN ({', '.join(repr(p) for p in self.props)})"

    def D(self, col: str, prev: bool = False) -> str:
        s, e = (self.cs, self.ce) if prev else (self.start, self.end)
        return f"{col} BETWEEN DATE '{s}' AND DATE '{e}'"

    def df(self, sql: str) -> pd.DataFrame:
        return self.con.execute(sql).df()

    def val(self, sql: str):
        r = self.con.execute(sql).fetchall()
        return None if not r or r[0][0] is None else float(r[0][0])

    def pair(self, table: str, col: str, exprs: dict, where: str = "TRUE", pcol: str = "property_id") -> dict:
        """Evaluate aggregate expressions for the selected period and the comparison period."""
        out = {k: [] for k in exprs}
        sel = ", ".join(f"{e} AS k{i}" for i, e in enumerate(exprs.values()))
        for prev in (False, True):
            row = self.con.execute(f"SELECT {sel} FROM {table} WHERE {self.P(pcol)} AND {self.D(col, prev)} AND {where}").fetchall()[0]
            for k, v in zip(exprs, row):
                out[k].append(None if v is None else float(v))
        return out

    def budget(self, account_type: str, prev: bool = False, extra: str = "TRUE") -> float | None:
        s, e = (self.cs, self.ce) if prev else (self.start, self.end)
        return self.val(f"""SELECT sum(budget_usd * (date_diff('day', greatest(period_start, DATE '{s}'), least(last_day(period_start), DATE '{e}')) + 1) / day(last_day(period_start)))
                            FROM gold.fact_budget WHERE {self.P()} AND account_type = '{account_type}' AND period_start <= DATE '{e}' AND last_day(period_start) >= DATE '{s}' AND {extra}""")

    def bucket(self, df: pd.DataFrame, col: str = "d") -> pd.DataFrame:
        """Roll a daily frame up to the page grain (day / week / month)."""
        df = df.copy()
        df[col] = pd.to_datetime(df[col])
        if self.grain != "day" and len(df):
            df[col] = df[col].dt.to_period("W-SUN" if self.grain == "week" else "M").dt.start_time
            df = df.groupby(col, as_index=False).sum(numeric_only=True)
        return df.sort_values(col)

    def forecast(self, metric: str, days: int = 30, where: str = "TRUE", kind: str = "forecast") -> pd.DataFrame:
        cond = f"horizon_day <= {days}" if kind == "forecast" else self.D("business_date")
        return self.df(f"""SELECT business_date AS d, sum(forecast_value) AS v, sum(lower_80) AS lo, sum(upper_80) AS hi, sum(actual_value) AS actual, sum(capacity) AS cap
                           FROM gold.fact_forecasts WHERE metric = '{metric}' AND forecast_type = '{kind}' AND {self.P()} AND {cond} AND {where} GROUP BY 1 ORDER BY 1""")

    def recs(self, vertical: str | None, n: int = 3) -> list[dict]:
        w = f"AND business_vertical = '{vertical}'" if vertical else ""
        df = self.df(f"""SELECT recommendation_id, title, priority, estimated_impact_usd, business_vertical, property_id FROM gold.fact_ai_recommendations
                         WHERE ({self.P()} {"OR property_id = 'ALL'" if self.scope.enterprise else ""}) {w} ORDER BY priority_score DESC LIMIT {n}""")
        return [r for r in df.to_dict("records") if self.scope.sees(r["property_id"], r["business_vertical"])]


# ----------------------------------------------------------------------------- spec helpers
def div(a, b):
    return None if a is None or not b else a / b


def ratio(m: dict, a: str, b: str) -> list:
    return [div(m[a][0], m[b][0]), div(m[a][1], m[b][1])]


def kpi(c: Ctx, label: str, pair, fmt: str = "num", good: str = "up", defn: str | None = None) -> dict:
    return {"label": label, "value": pair[0], "prev": pair[1], "fmt": fmt, "good": good, "definition": c.defs.get(defn or label)}


def tline(c: Ctx, title: str, series: list, fmt: str = "num", span: int = 6, note: str | None = None, bucket: bool = True) -> dict:
    """series: list of (name, kind, frame[d, v(, lo, hi)]); kind in actual | compare | budget | forecast | capacity."""
    frames = [(n, k, (c.bucket(f) if bucket and k not in ("ratio",) else f.assign(d=pd.to_datetime(f["d"])))) for n, k, f in series if f is not None and len(f)]
    xs = sorted({d for _, _, f in frames for d in f["d"]})
    out = []
    for n, k, f in frames:
        f = f.set_index("d")
        s = {"name": n, "kind": "actual" if k == "ratio" else k, "data": [f["v"].get(d) for d in xs]}
        if "lo" in f and k == "forecast":
            s["lo"], s["hi"] = [f["lo"].get(d) for d in xs], [f["hi"].get(d) for d in xs]
        out.append(s)
    return {"type": "line", "title": title, "x": [d.date().isoformat() for d in xs], "series": out, "fmt": fmt, "span": span, "note": note, "grain": c.grain}


def bar(title: str, df: pd.DataFrame, cat: str, cols: dict, fmt: str = "num", span: int = 6, stacked: bool = False, horizontal: bool = False, note: str | None = None, kinds: dict | None = None) -> dict:
    return {"type": "bar", "title": title, "categories": df[cat].tolist(), "series": [{"name": n, "data": df[col].tolist(), "kind": (kinds or {}).get(n, "actual")} for n, col in cols.items()],
            "fmt": fmt, "span": span, "stacked": stacked, "horizontal": horizontal, "note": note}


def table(title: str, df: pd.DataFrame, cols: list, span: int = 6, note: str | None = None, drill: str | None = None) -> dict:
    """cols: (key, label, fmt). drill: page to open when a row with a property_id is clicked."""
    keep = [k for k, _, _ in cols] + (["property_id"] if drill and "property_id" in df.columns and "property_id" not in [k for k, _, _ in cols] else [])
    return {"type": "table", "title": title, "columns": [{"key": k, "label": l, "fmt": f} for k, l, f in cols], "rows": df[keep].to_dict("records"), "span": span, "note": note, "drill": drill}


def heat(title: str, df: pd.DataFrame, x: str, y: str, v: str, fmt: str = "num", span: int = 6, note: str | None = None, xorder=None, yorder=None) -> dict:
    p = df.pivot_table(index=y, columns=x, values=v, aggfunc="sum")
    p = p.reindex(index=yorder or list(p.index), columns=xorder or list(p.columns))
    return {"type": "heatmap", "title": title, "x": [str(a) for a in p.columns], "y": [str(a) for a in p.index], "values": p.values.tolist(), "fmt": fmt, "span": span, "note": note}


def fmtv(v, fmt: str) -> str:
    if v is None:
        return "n/a"
    if fmt == "usd":
        return f"${v / 1e6:,.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}K" if abs(v) >= 1e4 else f"${v:,.0f}"
    if fmt == "pct":
        return f"{v:.1%}"
    if fmt.startswith("usd"):
        return f"${v:,.0f}"
    return f"{v:,.0f}"


def movement(c: Ctx, label: str, pair, fmt: str, good: str = "up", measure: str | None = None) -> dict | None:
    cur, prev = pair
    if cur is None or prev in (None, 0):
        return None
    if fmt == "pct":
        d, txt = cur - prev, f"{abs(cur - prev) * 100:.1f} points"
    else:
        d, txt = cur / prev - 1, f"{abs(cur / prev - 1):.1%}"
    if abs(d) < (0.005 if fmt == "pct" else 0.01):
        return {"text": f"{label} is flat at {fmtv(cur, fmt)} versus the {c.clabel}.", "tone": "neutral", "measure": measure or label}
    return {"text": f"{label} is {'up' if d > 0 else 'down'} {txt} to {fmtv(cur, fmt)} versus the {c.clabel} ({fmtv(prev, fmt)}).",
            "tone": "good" if (d > 0) == (good == "up") else "bad", "measure": measure or label}


def driver(c: Ctx, label: str, table_: str, col: str, expr: str, fmt: str = "usd", where: str = "TRUE") -> dict | None:
    """Name the property that moved a measure the most between the two periods."""
    df = c.df(f"""SELECT property_id, {expr} FILTER (WHERE {c.D(col)}) AS cur, {expr} FILTER (WHERE {c.D(col, True)}) AS prev FROM {table_} WHERE {c.P()} AND {where} GROUP BY 1""")
    df = df.dropna()
    if len(df) < 2:
        return None
    df["d"] = df["cur"] - df["prev"]
    r = df.loc[df["d"].abs().idxmax()]
    return {"text": f"{c.names[r['property_id']]} moved {label.lower()} the most: {fmtv(r['prev'], fmt)} to {fmtv(r['cur'], fmt)} ({r['d'] / r['prev']:+.1%})." if r["prev"] else None,
            "tone": "good" if r["d"] > 0 else "bad", "measure": label}


def page(c: Ctx, title: str, subtitle: str, kpis: list, panels: list, insights: list, vertical: str | None) -> dict:
    return {"title": title, "subtitle": subtitle, "kpis": kpis, "panels": [p for p in panels if p], "insights": [i for i in insights if i and i.get("text")],
            "recommendations": c.recs(vertical), "period": {"start": c.start, "end": c.end, "compare_start": c.cs, "compare_end": c.ce, "compare_label": c.clabel, "grain": c.grain},
            "properties": c.props}


# ----------------------------------------------------------------------------- executive
def executive(c: Ctx) -> dict:
    m = c.pair("gold.mart_executive_daily", "business_date", {
        "rev": "sum(total_revenue_usd)", "room": "sum(room_revenue_usd)", "sold": "sum(rooms_sold)", "avail": "sum(rooms_available)", "fb": "sum(restaurant_revenue_usd)",
        "wp": "sum(waterpark_revenue_usd)", "rt": "sum(retail_revenue_usd)", "spa": "sum(spa_revenue_usd)", "glrev": "sum(gl_revenue_usd)", "opex": "sum(opex_usd)", "gop": "sum(gop_usd)"})
    nps = c.pair("gold.fact_guest_feedback", "survey_date", {"nps": "(count(*) FILTER (WHERE nps_score >= 9) - count(*) FILTER (WHERE nps_score <= 6)) * 100.0 / nullif(count(*), 0)"})["nps"]
    bud = [c.budget("EXPENSE"), c.budget("EXPENSE", True)]
    bt = c.forecast("finance_revenue", kind="backtest")
    fva = [div(bt["actual"].sum(), bt["v"].sum()) - 1 if len(bt) and bt["v"].sum() else None, None]
    kpis = [kpi(c, "Total revenue", m["rev"], "usd"), kpi(c, "Room revenue", m["room"], "usd"), kpi(c, "Occupancy", ratio(m, "sold", "avail"), "pct", defn="Occupancy %"),
            kpi(c, "ADR", ratio(m, "room", "sold"), "usd0"), kpi(c, "RevPAR", ratio(m, "room", "avail"), "usd0"), kpi(c, "Restaurant revenue", m["fb"], "usd"),
            kpi(c, "Waterpark revenue", m["wp"], "usd"), kpi(c, "Retail revenue", m["rt"], "usd"), kpi(c, "Spa & leisure revenue", m["spa"], "usd"),
            kpi(c, "GOP", m["gop"], "usd"), kpi(c, "GOPPAR", ratio(m, "gop", "avail"), "usd0"), kpi(c, "Operating expenditure", m["opex"], "usd", good="down"),
            kpi(c, "OPEX vs budget", [div(m["opex"][0], bud[0]) - 1 if bud[0] else None, div(m["opex"][1], bud[1]) - 1 if bud[1] else None], "signed_pct", good="down", defn="Budget Variance"),
            kpi(c, "Revenue vs forecast", fva, "signed_pct"), kpi(c, "Guest NPS", nps, "dec0", defn="NPS")]
    daily = c.df(f"SELECT business_date AS d, sum(total_revenue_usd) AS v, sum(gop_usd) AS gop, sum(gl_revenue_usd) AS glrev FROM gold.mart_executive_daily WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1")
    ext = c.end + timedelta(days=30 if c.live and c.grain == "day" else 0)
    budg = c.df(f"""SELECT d.date AS d, sum(b.budget_usd / day(last_day(b.period_start))) AS v FROM gold.dim_date d JOIN gold.fact_budget b ON b.fiscal_period = d.year_month
                    WHERE b.account_type = 'REVENUE' AND {c.P('b.property_id')} AND d.date BETWEEN DATE '{c.start}' AND DATE '{ext}' GROUP BY 1""")
    fc = c.forecast("finance_revenue") if c.live and c.grain == "day" else None
    gfc = c.forecast("finance_gop") if c.live and c.grain == "day" else None
    byprop = c.df(f"""
        SELECT e.property_id, p.short_name AS property, p.region, p.brand_name, sum(total_revenue_usd) AS revenue, sum(room_revenue_usd) AS room, sum(restaurant_revenue_usd) AS fb,
               sum(waterpark_revenue_usd) AS wp, sum(retail_revenue_usd) AS retail, sum(spa_revenue_usd) AS spa, sum(rooms_sold) / sum(rooms_available) AS occupancy,
               sum(room_revenue_usd) / nullif(sum(rooms_sold), 0) AS adr, sum(room_revenue_usd) / sum(rooms_available) AS revpar, sum(gop_usd) AS gop, sum(gop_usd) / nullif(sum(gl_revenue_usd), 0) AS gop_margin,
               sum(opex_usd) AS opex
        FROM gold.mart_executive_daily e JOIN gold.dim_property p USING (property_id) WHERE {c.P('e.property_id')} AND {c.D('business_date')} GROUP BY ALL ORDER BY revenue DESC""")
    prev = c.df(f"SELECT property_id, sum(total_revenue_usd) AS prev FROM gold.mart_executive_daily WHERE {c.P()} AND {c.D('business_date', True)} GROUP BY 1")
    byprop = byprop.merge(prev, on="property_id", how="left")
    byprop["change"] = byprop["revenue"] / byprop["prev"] - 1
    cc = c.df(f"""SELECT cost_centre, sum(amount_usd) AS actual FROM gold.fact_financial_transactions WHERE {c.P()} AND account_type = 'EXPENSE' AND {c.D('posting_date')} GROUP BY 1 ORDER BY 2 DESC""")
    cc["budget"] = [c.budget("EXPENSE", extra=f"cost_centre = '{x}'") for x in cc["cost_centre"]]
    region = byprop.groupby("region", as_index=False)[["revenue", "gop"]].sum()
    mix = pd.DataFrame({"vertical": ["Hotels", "Restaurants", "Waterpark", "Retail", "Spa & Leisure"], "cur": [m[k][0] for k in ("room", "fb", "wp", "rt", "spa")], "prev": [m[k][1] for k in ("room", "fb", "wp", "rt", "spa")]})
    panels = [
        tline(c, "Revenue: actual, budget and forecast", [("Actual", "actual", daily[["d", "v"]]), ("Budget", "budget", budg), ("Forecast", "forecast", fc)], "usd", 8,
              "Forecast shows the next 30 days with an 80% interval." if fc is not None else None),
        bar("Business vertical contribution", mix, "vertical", {"Selected period": "cur", c.clabel.capitalize(): "prev"}, "usd", 4, kinds={c.clabel.capitalize(): "compare"}),
        table("Property comparison", byprop, [("property", "Property", "text"), ("revenue", "Revenue", "usd"), ("change", "vs " + c.clabel, "signed_pct"), ("occupancy", "Occupancy", "pct"),
                                                ("adr", "ADR", "usd0"), ("revpar", "RevPAR", "usd0"), ("gop", "GOP", "usd"), ("gop_margin", "GOP margin", "pct")], 8,
              "Select a property to open its hotel performance.", drill="hotels"),
        bar("Revenue by vertical and property", byprop, "property", {"Hotels": "room", "Restaurants": "fb", "Waterpark": "wp", "Retail": "retail", "Spa & Leisure": "spa"}, "usd", 4, stacked=True),
        tline(c, "Gross operating profit: actual and forecast", [("Actual", "actual", daily.rename(columns={"v": "x", "gop": "v"})[["d", "v"]]), ("Forecast", "forecast", gfc)], "usd", 6),
        bar("Operating expenditure: actual versus budget", cc, "cost_centre", {"Actual": "actual", "Budget": "budget"}, "usd", 6, horizontal=True, kinds={"Budget": "budget"}),
        bar("Revenue and GOP by region", region, "region", {"Revenue": "revenue", "GOP": "gop"}, "usd", 4) if len(region) > 1 else None,
    ]
    weakest = byprop.loc[byprop["gop_margin"].idxmin()] if len(byprop) > 1 else None
    insights = [movement(c, "Total revenue", m["rev"], "usd"), driver(c, "Total revenue", "gold.mart_executive_daily", "business_date", "sum(total_revenue_usd)"),
                movement(c, "RevPAR", ratio(m, "room", "avail"), "usd0"), movement(c, "GOP", m["gop"], "usd"),
                {"text": f"Operating expenditure is {abs(kpis[12]['value']):.1%} {'over' if kpis[12]['value'] > 0 else 'under'} the pro-rated budget for the period.",
                 "tone": "bad" if kpis[12]["value"] > 0.02 else "good", "measure": "OPEX vs budget"} if kpis[12]["value"] is not None else None,
                {"text": f"{weakest['property']} has the lowest GOP margin at {weakest['gop_margin']:.1%}.", "tone": "bad", "measure": "GOP margin"} if weakest is not None else None]
    return page(c, "Executive Leadership", "Enterprise performance across all properties and business verticals", kpis, panels, insights, None)


# ----------------------------------------------------------------------------- hotels
def hotels(c: Ctx) -> dict:
    m = c.pair("gold.fact_room_occupancy", "business_date", {"sold": "sum(rooms_sold)", "avail": "sum(rooms_available)", "rev": "sum(room_revenue_usd)"})
    r = c.pair("gold.fact_reservations", "arrival_date", {
        "n": "count(*)", "canc": "count(*) FILTER (WHERE status = 'CANCELLED')", "ns": "count(*) FILTER (WHERE status = 'NO_SHOW')", "lead": "avg(lead_days)",
        "los": "avg(nights) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN'))"})
    kpis = [kpi(c, "Occupancy", ratio(m, "sold", "avail"), "pct", defn="Occupancy %"), kpi(c, "ADR", ratio(m, "rev", "sold"), "usd0"), kpi(c, "RevPAR", ratio(m, "rev", "avail"), "usd0"),
            kpi(c, "Room revenue", m["rev"], "usd"), kpi(c, "Rooms sold", m["sold"], "num"), kpi(c, "Cancellation rate", ratio(r, "canc", "n"), "pct", good="down"),
            kpi(c, "No-show rate", ratio(r, "ns", "n"), "pct", good="down"), kpi(c, "Booking lead time (days)", r["lead"], "dec1", good="neutral"), kpi(c, "Length of stay (nights)", r["los"], "dec2", good="neutral")]
    d = c.bucket(c.df(f"SELECT business_date AS d, sum(rooms_sold) AS sold, sum(rooms_available) AS avail, sum(room_revenue_usd) AS rev FROM gold.fact_room_occupancy WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1"))
    occ = d.assign(v=d["sold"] / d["avail"])[["d", "v"]]
    fc = None
    if c.live and c.grain == "day":
        fc = c.forecast("hotel_rooms_sold", 30, "entity_id = 'ALL'")
        fc = fc.assign(v=fc["v"] / fc["cap"], lo=fc["lo"] / fc["cap"], hi=fc["hi"] / fc["cap"])
    otb = c.df(f"""SELECT o.business_date AS d, sum(o.rooms_on_books) AS otb, sum(o.rooms_available) AS cap FROM gold.fact_room_occupancy o
                   WHERE {c.P()} AND o.business_date > DATE '{AS_OF}' AND o.business_date <= DATE '{AS_OF}' + INTERVAL 60 DAY GROUP BY 1""")
    pick = c.df(f"""SELECT stay_date AS d, count(*) AS v FROM silver.room_night n JOIN gold.fact_reservations r USING (reservation_id)
                    WHERE {c.P('n.property_id')} AND NOT n.is_actual AND n.stay_date <= DATE '{AS_OF}' + INTERVAL 60 DAY AND CAST(r.booking_ts AS DATE) > DATE '{AS_OF}' - INTERVAL 7 DAY GROUP BY 1""")
    fc60 = c.forecast("hotel_rooms_sold", 60, "entity_id = 'ALL'")
    ch = c.df(f"""
        WITH rv AS (SELECT channel, sum(room_nights) AS room_nights, sum(room_revenue_usd) AS revenue FROM gold.fact_room_revenue WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1),
             cx AS (SELECT channel, count(*) FILTER (WHERE status = 'CANCELLED') * 1.0 / count(*) AS cancel_rate, avg(lead_days) AS lead FROM gold.fact_reservations WHERE {c.P()} AND {c.D('arrival_date')} GROUP BY 1)
        SELECT rv.*, revenue / room_nights AS adr, revenue / sum(revenue) OVER () AS share, cx.cancel_rate, cx.lead FROM rv LEFT JOIN cx USING (channel) ORDER BY revenue DESC""")
    rt = c.df(f"""SELECT r.room_type_name AS room_type, sum(rooms_sold) / sum(rooms_available) AS occupancy, sum(room_revenue_usd) / nullif(sum(rooms_sold), 0) AS adr,
                         sum(room_revenue_usd) / sum(rooms_available) AS revpar, sum(room_revenue_usd) AS revenue
                  FROM gold.fact_room_occupancy o JOIN gold.dim_room r USING (room_type) WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1 ORDER BY adr""")
    seg = c.df(f"SELECT segment, sum(room_revenue_usd) AS revenue, sum(room_nights) AS room_nights FROM gold.fact_room_revenue WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1 ORDER BY 2 DESC")
    tier = c.df(f"""SELECT coalesce(g.loyalty_tier, 'Non-member') AS tier, count(*) AS stays, sum(r.room_revenue_usd) AS revenue, sum(r.room_revenue_usd) / sum(r.nights) AS adr
                    FROM gold.fact_reservations r LEFT JOIN gold.dim_guest g USING (guest_key) WHERE {c.P('r.property_id')} AND {c.D('r.arrival_date')} AND r.status IN {OCC} GROUP BY 1 ORDER BY revenue DESC""")
    cx = c.bucket(c.df(f"""SELECT arrival_date AS d, count(*) AS n, count(*) FILTER (WHERE status = 'CANCELLED') AS canc, count(*) FILTER (WHERE status = 'NO_SHOW') AS ns
                           FROM gold.fact_reservations WHERE {c.P()} AND {c.D('arrival_date')} GROUP BY 1"""))
    panels = [
        tline(c, "Occupancy trend and forecast", [("Actual", "ratio", occ), ("Forecast", "forecast", fc)], "pct", 6, bucket=False),
        tline(c, "ADR and RevPAR", [("ADR", "ratio", d.assign(v=d["rev"] / d["sold"])[["d", "v"]]), ("RevPAR", "ratio", d.assign(v=d["rev"] / d["avail"])[["d", "v"]])], "usd0", 6, bucket=False),
        tline(c, "Reservation pickup: next 60 days", [("On the books", "actual", otb.rename(columns={"otb": "v"})[["d", "v"]]), ("Picked up in last 7 days", "compare", pick),
                                                       ("Forecast rooms sold", "forecast", fc60[["d", "v", "lo", "hi"]]), ("Sellable rooms", "capacity", otb.rename(columns={"cap": "v"})[["d", "v"]])],
              "num", 8, "Rooms already booked for future nights against the occupancy forecast and sellable capacity.", bucket=False),
        bar("Guest segmentation: revenue by market segment", seg, "segment", {"Room revenue": "revenue"}, "usd", 4),
        table("Booking channel performance", ch, [("channel", "Channel", "text"), ("room_nights", "Room nights", "num"), ("revenue", "Revenue", "usd"), ("share", "Share", "pct"),
                                                   ("adr", "ADR", "usd0"), ("cancel_rate", "Cancel rate", "pct"), ("lead", "Lead days", "dec1")], 7),
        table("Room category performance", rt, [("room_type", "Category", "text"), ("occupancy", "Occupancy", "pct"), ("adr", "ADR", "usd0"), ("revpar", "RevPAR", "usd0"), ("revenue", "Revenue", "usd")], 5),
        tline(c, "Cancellation and no-show rates by arrival date", [("Cancellation rate", "ratio", cx.assign(v=cx["canc"] / cx["n"])[["d", "v"]]),
                                                                    ("No-show rate", "ratio", cx.assign(v=cx["ns"] / cx["n"])[["d", "v"]])], "pct", 7, bucket=False),
        table("Loyalty tier mix", tier, [("tier", "Tier", "text"), ("stays", "Stays", "num"), ("revenue", "Revenue", "usd"), ("adr", "ADR", "usd0")], 5),
    ]
    top = ch.iloc[0] if len(ch) else None
    risky = ch.loc[ch["cancel_rate"].idxmax()] if len(ch) else None
    insights = [movement(c, "Occupancy", ratio(m, "sold", "avail"), "pct"), movement(c, "ADR", ratio(m, "rev", "sold"), "usd0"), movement(c, "RevPAR", ratio(m, "rev", "avail"), "usd0"),
                driver(c, "Room revenue", "gold.fact_room_occupancy", "business_date", "sum(room_revenue_usd)"),
                {"text": f"{top['channel']} is the largest channel with {top['share']:.0%} of room revenue; {risky['channel']} has the highest cancellation rate at {risky['cancel_rate']:.0%}.",
                 "tone": "neutral", "measure": "Booking channel performance"} if top is not None else None]
    return page(c, "Hotel Performance", "Occupancy, rate, pickup, channels and guest mix", kpis, panels, insights, "Hotels")


# ----------------------------------------------------------------------------- restaurants
def restaurants(c: Ctx) -> dict:
    m = c.pair("gold.fact_restaurant_covers", "business_date", {"covers": "sum(covers)", "rev": "sum(net_usd)", "cost": "sum(food_cost_usd)", "walk": "sum(walk_in_covers)", "checks": "sum(checks)"})
    seats = c.val(f"SELECT sum(capacity * turns_per_day) FROM gold.dim_outlet WHERE outlet_type = 'Restaurant' AND {c.P()}") or 0
    tr = c.pair("gold.fact_table_reservations", "business_date", {"n": "count(*)", "ns": "count(*) FILTER (WHERE status = 'NO_SHOW')", "cx": "count(*) FILTER (WHERE status = 'CANCELLED')"})
    util = [div(m["covers"][0], seats * c.days), div(m["covers"][1], seats * c.days)]
    kpis = [kpi(c, "Covers", m["covers"], "num"), kpi(c, "Restaurant revenue", m["rev"], "usd"), kpi(c, "Spend per guest", ratio(m, "rev", "covers"), "usd0", defn="Spend per Guest"),
            kpi(c, "Seat utilisation", util, "pct", defn="Seat Utilisation"), kpi(c, "Food cost", ratio(m, "cost", "rev"), "pct", good="down", defn="Food Cost %"),
            kpi(c, "Walk-in share of covers", ratio(m, "walk", "covers"), "pct", good="neutral"), kpi(c, "Table cancellation rate", ratio(tr, "cx", "n"), "pct", good="down"),
            kpi(c, "Table no-show rate", ratio(tr, "ns", "n"), "pct", good="down")]
    by = c.df(f"""SELECT o.outlet_name AS outlet, p.short_name AS property, o.sub_type AS concept, sum(f.covers) AS covers, sum(f.net_usd) AS revenue, sum(f.net_usd) / sum(f.covers) AS spend,
                         sum(f.food_cost_usd) / sum(f.net_usd) AS food_cost, sum(f.covers) / (any_value(o.capacity * o.turns_per_day) * {c.days}) AS utilisation
                  FROM gold.fact_restaurant_covers f JOIN gold.dim_outlet o USING (outlet_code) JOIN gold.dim_property p ON p.property_id = f.property_id
                  WHERE {c.P('f.property_id')} AND {c.D('f.business_date')} GROUP BY ALL ORDER BY revenue DESC""")
    slot = c.df(f"SELECT time_slot, sum(net_usd) AS revenue, sum(covers) AS covers FROM gold.fact_restaurant_covers WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1")
    slot = slot.set_index("time_slot").reindex(["Breakfast", "Lunch", "Afternoon", "Dinner"]).dropna().reset_index()
    hm = c.df(f"SELECT strftime(business_date, '%a') AS dow, time_slot, sum(covers) * 1.0 / count(DISTINCT business_date) AS covers FROM gold.fact_restaurant_covers WHERE {c.P()} AND {c.D('business_date')} GROUP BY ALL")
    menu = c.df(f"""SELECT m.item_name AS item, m.category, sum(l.qty) AS qty, sum(l.net_usd) AS revenue, 1 - sum(l.food_cost_usd) / sum(l.net_usd) AS margin, sum(l.net_usd - l.food_cost_usd) AS contribution
                    FROM gold.fact_restaurant_transactions l JOIN silver.menu_item m USING (menu_item_code) WHERE {c.P('l.property_id')} AND {c.D('l.business_date')} GROUP BY ALL""")
    if len(menu):
        pop, mar = menu["qty"].median(), (menu["contribution"].sum() / menu["revenue"].sum())
        menu["class"] = [("Star" if q >= pop and g >= mar else "Plowhorse" if q >= pop else "Puzzle" if g >= mar else "Dog") for q, g in zip(menu["qty"], menu["margin"])]
        menu = menu.sort_values("contribution", ascending=False).head(14)
    daily = c.df(f"SELECT business_date AS d, sum(covers) AS v FROM gold.fact_restaurant_covers WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1")
    bt = c.forecast("restaurant_covers", kind="backtest")
    fc = c.forecast("restaurant_covers", 30) if c.live and c.grain == "day" else None
    panels = [
        bar("Covers by restaurant", by, "outlet", {"Covers": "covers"}, "num", 6, horizontal=True),
        tline(c, "Forecast versus actual covers", [("Actual", "actual", daily), ("Forecast made 90 days ago", "compare", bt[["d", "v"]] if len(bt) else None), ("Forecast", "forecast", fc)], "num", 6,
              "The dashed line is the backtest forecast for days that have since closed."),
        table("Revenue by outlet", by, [("outlet", "Outlet", "text"), ("property", "Property", "text"), ("concept", "Concept", "text"), ("covers", "Covers", "num"), ("revenue", "Revenue", "usd"),
                                        ("spend", "Spend / guest", "usd0"), ("food_cost", "Food cost", "pct"), ("utilisation", "Seat utilisation", "pct")], 8),
        bar("Revenue by time slot", slot, "time_slot", {"Revenue": "revenue"}, "usd", 4),
        table("Menu performance", menu, [("item", "Menu item", "text"), ("category", "Category", "text"), ("qty", "Sold", "num"), ("revenue", "Revenue", "usd"), ("margin", "Margin", "pct"),
                                         ("contribution", "Contribution", "usd"), ("class", "Class", "tag")], 7,
              "Menu engineering classes: Star (popular, high margin), Plowhorse (popular, low margin), Puzzle (unpopular, high margin), Dog (unpopular, low margin)."),
        heat("Average covers by day and time slot", hm, "time_slot", "dow", "covers", "num", 5, xorder=["Breakfast", "Lunch", "Afternoon", "Dinner"], yorder=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
    ]
    best = by.loc[by["utilisation"].idxmax()] if len(by) else None
    insights = [movement(c, "Covers", m["covers"], "num"), movement(c, "Spend per guest", ratio(m, "rev", "covers"), "usd0"), movement(c, "Food cost", ratio(m, "cost", "rev"), "pct", good="down"),
                {"text": f"{best['outlet']} runs the highest seat utilisation at {best['utilisation']:.0%}; {by.loc[by['utilisation'].idxmin(), 'outlet']} has the most spare capacity at {by['utilisation'].min():.0%}.",
                 "tone": "neutral", "measure": "Seat utilisation"} if best is not None and len(by) > 1 else None]
    return page(c, "Restaurant Performance", "Covers, spend, table utilisation and menu profitability", kpis, panels, insights, "Restaurants")


# ----------------------------------------------------------------------------- waterpark
def waterpark(c: Ctx) -> dict:
    m = c.pair("gold.fact_waterpark_daily", "business_date", {"vis": "sum(visitors)", "rev": "sum(net_usd)", "cap": "sum(daily_capacity)", "peak": "max(visitors)", "q": "sum(avg_queue_minutes * rides) / nullif(sum(rides), 0)",
                                                               "rides": "sum(rides)", "rcap": "sum(ride_capacity)", "txn": "sum(transactions)"})
    if not m["vis"][0]:
        return page(c, "Waterpark Performance", "The selected properties do not operate a waterpark", [], [], [{"text": "Select Dubai Palm or Phuket to see waterpark performance.", "tone": "neutral", "measure": ""}], "Waterpark")
    kpis = [kpi(c, "Attendance", m["vis"], "num"), kpi(c, "Ticket revenue", m["rev"], "usd"), kpi(c, "Revenue per visitor", ratio(m, "rev", "vis"), "usd1"),
            kpi(c, "Capacity utilisation", ratio(m, "vis", "cap"), "pct", defn="Capacity Utilisation"), kpi(c, "Peak daily attendance", m["peak"], "num", good="neutral"),
            kpi(c, "Attraction utilisation", ratio(m, "rides", "rcap"), "pct", defn="Attraction Utilisation"), kpi(c, "Average queue (minutes)", m["q"], "dec1", good="down"),
            kpi(c, "Ticket transactions", m["txn"], "num")]
    daily = c.df(f"SELECT business_date AS d, sum(visitors) AS v, sum(daily_capacity) AS cap FROM gold.fact_waterpark_daily WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1")
    bt = c.forecast("waterpark_attendance", kind="backtest")
    fc = c.forecast("waterpark_attendance", 30) if c.live and c.grain == "day" else None
    capline = pd.concat([daily[["d", "cap"]].rename(columns={"cap": "v"}), fc[["d", "cap"]].rename(columns={"cap": "v"}) if fc is not None else None])
    cat = c.df(f"SELECT ticket_category, sum(visitors) AS visitors, sum(net_usd) AS revenue, sum(net_usd) / sum(visitors) AS yield FROM gold.fact_waterpark_attendance WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1 ORDER BY revenue DESC")
    chn = c.df(f"SELECT sales_channel, sum(visitors) AS visitors, sum(net_usd) AS revenue FROM gold.fact_waterpark_attendance WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1 ORDER BY revenue DESC")
    att = c.df(f"""SELECT a.attraction_name AS attraction, p.short_name AS property, sum(u.riders) AS riders, sum(u.riders) * 1.0 / sum(u.capacity) AS utilisation,
                          sum(u.avg_queue_minutes * u.riders) / nullif(sum(u.riders), 0) AS avg_queue, max(u.avg_queue_minutes) AS peak_queue
                   FROM gold.fact_attraction_utilisation u JOIN gold.dim_attraction a USING (attraction_code) JOIN gold.dim_property p ON p.property_id = u.property_id
                   WHERE {c.P('u.property_id')} AND {c.D('u.business_date')} GROUP BY ALL ORDER BY utilisation DESC""")
    hm = c.df(f"""SELECT a.attraction_name AS attraction, lpad(CAST(u.hour_of_day AS VARCHAR), 2, '0') || ':00' AS hour, avg(u.avg_queue_minutes) AS q
                  FROM gold.fact_attraction_utilisation u JOIN gold.dim_attraction a USING (attraction_code) WHERE {c.P('u.property_id')} AND {c.D('u.business_date')} GROUP BY ALL""")
    hours = c.df(f"SELECT lpad(CAST(hour_of_day AS VARCHAR), 2, '0') || ':00' AS hour, sum(riders) * 1.0 / count(DISTINCT business_date) AS riders FROM gold.fact_attraction_utilisation WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1 ORDER BY 1")
    panels = [
        tline(c, "Daily attendance, capacity and forecast", [("Actual", "actual", daily[["d", "v"]]), ("Forecast made 90 days ago", "compare", bt[["d", "v"]] if len(bt) else None),
                                                              ("Forecast", "forecast", fc), ("Licensed capacity", "capacity", capline)], "num", 8),
        bar("Ticket sales by category", cat, "ticket_category", {"Revenue": "revenue"}, "usd", 4),
        heat("Average queue time by attraction and hour (minutes)", hm, "hour", "attraction", "q", "dec1", 7),
        bar("Visitor traffic by hour (average rides per hour)", hours, "hour", {"Rides": "riders"}, "num", 5),
        table("Attraction utilisation", att, [("attraction", "Attraction", "text"), ("property", "Property", "text"), ("riders", "Riders", "num"), ("utilisation", "Utilisation", "pct"),
                                              ("avg_queue", "Avg queue (min)", "dec1"), ("peak_queue", "Peak queue (min)", "dec0")], 7),
        table("Ticket sales channels", chn, [("sales_channel", "Channel", "text"), ("visitors", "Visitors", "num"), ("revenue", "Revenue", "usd")], 5),
    ]
    busy = c.val(f"SELECT count(*) FROM gold.fact_waterpark_daily WHERE {c.P()} AND {c.D('business_date')} AND capacity_utilisation >= 0.9") or 0
    insights = [movement(c, "Attendance", m["vis"], "num"), movement(c, "Revenue per visitor", ratio(m, "rev", "vis"), "usd0"),
                {"text": f"{busy:.0f} park-day(s) ran at 90% of licensed capacity or more in the period.", "tone": "bad" if busy else "good", "measure": "Capacity utilisation"},
                {"text": f"{att.iloc[att['avg_queue'].argmax()]['attraction']} has the longest average queue at {att['avg_queue'].max():.0f} minutes.", "tone": "neutral", "measure": "Queue duration"} if len(att) else None]
    return page(c, "Waterpark Performance", "Attendance, capacity, attraction throughput and queues", kpis, panels, insights, "Waterpark")


# ----------------------------------------------------------------------------- retail
def retail(c: Ctx) -> dict:
    m = c.pair("gold.fact_retail_sales", "business_date", {"rev": "sum(net_usd)", "txn": "count(DISTINCT txn_id)", "cost": "sum(cost_usd)", "units": "sum(qty)", "disc": "sum(discount_usd)", "gross": "sum(gross_usd)",
                                                            "known": "count(DISTINCT txn_id) FILTER (WHERE guest_key IS NOT NULL)"})
    inv = c.pair("gold.fact_inventory", "snapshot_date", {"so": "avg(is_stockout::INT)", "val": "sum(stock_value_usd) / count(DISTINCT snapshot_date)"})
    turn = [div(m["cost"][i] * 365 / c.days, inv["val"][i]) if m["cost"][i] else None for i in (0, 1)]
    slow = c.val(f"SELECT sum(stock_value_usd) FROM ai.inventory_risk WHERE slow_moving AND {c.P()}")
    kpis = [kpi(c, "Retail sales", m["rev"], "usd"), kpi(c, "Average transaction value", ratio(m, "rev", "txn"), "usd1", defn="Average Transaction Value"),
            kpi(c, "Contribution margin", [div((m["rev"][i] or 0) - (m["cost"][i] or 0), m["rev"][i]) for i in (0, 1)], "pct"), kpi(c, "Inventory turnover (annualised)", turn, "dec1x", defn="Inventory Turnover"),
            kpi(c, "Stock-out rate", inv["so"], "pct2", good="down", defn="Stock-out Rate"), kpi(c, "Units sold", m["units"], "num"), kpi(c, "Discount rate", ratio(m, "disc", "gross"), "pct", good="down"),
            kpi(c, "Slow-moving stock (at cost)", [slow, None], "usd", good="down")]
    store = c.df(f"""SELECT o.outlet_name AS store, p.short_name AS property, sum(s.net_usd) AS revenue, count(DISTINCT s.txn_id) AS txns, sum(s.net_usd) / count(DISTINCT s.txn_id) AS atv,
                            1 - sum(s.cost_usd) / sum(s.net_usd) AS margin, sum(s.discount_usd) / sum(s.gross_usd) AS discount_rate
                     FROM gold.fact_retail_sales s JOIN gold.dim_outlet o ON o.outlet_code = s.store_code JOIN gold.dim_property p ON p.property_id = s.property_id
                     WHERE {c.P('s.property_id')} AND {c.D('s.business_date')} GROUP BY ALL ORDER BY revenue DESC""")
    cat = c.df(f"""SELECT p.category, sum(s.net_usd) AS revenue, sum(s.net_usd - s.cost_usd) AS contribution, 1 - sum(s.cost_usd) / sum(s.net_usd) AS margin
                   FROM gold.fact_retail_sales s JOIN silver.product p USING (sku) WHERE {c.P('s.property_id')} AND {c.D('s.business_date')} GROUP BY 1 ORDER BY revenue DESC""")
    prod = c.df(f"""SELECT p.product_name AS product, p.category, sum(s.qty) AS units, sum(s.net_usd) AS revenue, 1 - sum(s.cost_usd) / sum(s.net_usd) AS margin, sum(s.net_usd - s.cost_usd) AS contribution
                    FROM gold.fact_retail_sales s JOIN silver.product p USING (sku) WHERE {c.P('s.property_id')} AND {c.D('s.business_date')} GROUP BY ALL ORDER BY contribution DESC LIMIT 12""")
    so = c.bucket(c.df(f"SELECT snapshot_date AS d, sum(is_stockout::INT) AS so, count(*) AS n FROM gold.fact_inventory WHERE {c.P()} AND {c.D('snapshot_date')} GROUP BY 1"))
    risk = c.df(f"""SELECT product_name AS product, outlet_name AS store, on_hand_qty AS on_hand, days_cover, lead_time_days AS lead_time, reorder_qty, stock_value_usd AS stock_value,
                           CASE WHEN at_risk THEN 'Stock-out risk' ELSE 'Slow-moving' END AS status
                    FROM ai.inventory_risk WHERE {c.P()} AND (at_risk OR slow_moving) ORDER BY at_risk DESC, CASE WHEN at_risk THEN days_cover ELSE -stock_value_usd END LIMIT 16""")
    beh = c.df(f"""SELECT coalesce(g.loyalty_tier, 'Unidentified shopper') AS shopper, count(DISTINCT s.txn_id) AS txns, sum(s.net_usd) AS revenue, sum(s.net_usd) / count(DISTINCT s.txn_id) AS atv,
                          sum(s.qty) * 1.0 / count(DISTINCT s.txn_id) AS basket
                   FROM gold.fact_retail_sales s LEFT JOIN gold.dim_guest g USING (guest_key) WHERE {c.P('s.property_id')} AND {c.D('s.business_date')} GROUP BY 1 ORDER BY revenue DESC""")
    daily = c.df(f"SELECT business_date AS d, sum(net_usd) AS v FROM gold.fact_retail_sales WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1")
    panels = [
        bar("Sales by store", store, "store", {"Sales": "revenue"}, "usd", 6, horizontal=True),
        bar("Sales and contribution by category", cat, "category", {"Sales": "revenue", "Contribution": "contribution"}, "usd", 6, kinds={"Contribution": "compare"}),
        tline(c, "Retail sales trend", [("Sales", "actual", daily)], "usd", 6),
        tline(c, "Stock-out rate", [("Stock-out rate", "ratio", so.assign(v=so["so"] / so["n"])[["d", "v"]])], "pct2", 6, bucket=False),
        table("Product contribution margin", prod, [("product", "Product", "text"), ("category", "Category", "text"), ("units", "Units", "num"), ("revenue", "Sales", "usd"), ("margin", "Margin", "pct"),
                                                    ("contribution", "Contribution", "usd")], 6),
        table("Inventory exceptions (latest snapshot)", risk, [("product", "Product", "text"), ("store", "Store", "text"), ("on_hand", "On hand", "num"), ("days_cover", "Days cover", "dec1"),
                                                               ("lead_time", "Lead time", "num"), ("reorder_qty", "Reorder qty", "num"), ("stock_value", "Stock at cost", "usd"), ("status", "Status", "tag")], 6,
              "Days of cover uses the retail demand forecast; latest snapshot regardless of the selected period."),
        table("Customer purchase behaviour", beh, [("shopper", "Shopper", "text"), ("txns", "Transactions", "num"), ("revenue", "Sales", "usd"), ("atv", "ATV", "usd1"), ("basket", "Units / basket", "dec2")], 6),
        table("Store performance", store, [("store", "Store", "text"), ("property", "Property", "text"), ("revenue", "Sales", "usd"), ("txns", "Transactions", "num"), ("atv", "ATV", "usd1"),
                                           ("margin", "Margin", "pct"), ("discount_rate", "Discount rate", "pct")], 6),
    ]
    insights = [movement(c, "Retail sales", m["rev"], "usd"), movement(c, "Average transaction value", ratio(m, "rev", "txn"), "usd0"), movement(c, "Discount rate", ratio(m, "disc", "gross"), "pct", good="down"),
                {"text": f"{div(m['known'][0], m['txn'][0]) or 0:.0%} of transactions are linked to an identified guest through room charge.", "tone": "neutral", "measure": "Customer purchase behaviour"}]
    return page(c, "Retail Performance", "Sales, margin, inventory health and shopper behaviour", kpis, panels, insights, "Retail")


# ----------------------------------------------------------------------------- spa
def spa(c: Ctx) -> dict:
    w = "status <> 'BOOKED'"
    m = c.pair("gold.fact_spa_appointments", "business_date", {
        "n": "count(*)", "done": "count(*) FILTER (WHERE status = 'COMPLETED')", "rev": "sum(net_usd)", "hrs": "sum(duration_minutes) FILTER (WHERE status = 'COMPLETED') / 60.0",
        "cx": "count(*) FILTER (WHERE status = 'CANCELLED')", "ns": "count(*) FILTER (WHERE status = 'NO_SHOW')", "g": "count(DISTINCT guest_key) FILTER (WHERE status = 'COMPLETED')"}, w)
    ther = c.val(f"SELECT sum(capacity) FROM gold.dim_outlet WHERE outlet_type = 'Spa' AND {c.P()}") or 0
    rep = []
    for prev in (False, True):
        s, e = (c.cs, c.ce) if prev else (c.start, c.end)
        rep.append(c.val(f"""WITH cur AS (SELECT DISTINCT guest_key FROM gold.fact_spa_appointments WHERE {c.P()} AND {c.D('business_date', prev)} AND status = 'COMPLETED' AND guest_key IS NOT NULL)
                             SELECT avg((EXISTS (SELECT 1 FROM gold.fact_spa_appointments h WHERE h.guest_key = cur.guest_key AND h.status = 'COMPLETED' AND h.business_date < DATE '{s}'))::INT) FROM cur"""))
    kpis = [kpi(c, "Completed appointments", m["done"], "num"), kpi(c, "Spa revenue", m["rev"], "usd"), kpi(c, "Revenue per appointment", ratio(m, "rev", "done"), "usd0"),
            kpi(c, "Therapist utilisation", [div(m["hrs"][0], ther * 10 * c.days), div(m["hrs"][1], ther * 10 * c.days)], "pct", defn="Spa Utilisation"),
            kpi(c, "Cancellation rate", ratio(m, "cx", "n"), "pct", good="down"), kpi(c, "No-show rate", ratio(m, "ns", "n"), "pct", good="down"),
            kpi(c, "Repeat customer rate", rep, "pct"), kpi(c, "Unique guests", m["g"], "num")]
    daily = c.df(f"SELECT business_date AS d, count(*) FILTER (WHERE status = 'COMPLETED') AS v FROM gold.fact_spa_appointments WHERE {c.P()} AND {c.D('business_date')} AND {w} GROUP BY 1")
    fc = c.forecast("spa_appointments", 30) if c.live and c.grain == "day" else None
    cat = c.df(f"SELECT service_category, sum(net_usd) AS revenue, count(*) FILTER (WHERE status = 'COMPLETED') AS appts FROM gold.fact_spa_appointments WHERE {c.P()} AND {c.D('business_date')} AND {w} GROUP BY 1 ORDER BY revenue DESC")
    svc = c.df(f"""SELECT service_name AS service, service_category AS category, count(*) FILTER (WHERE status = 'COMPLETED') AS appts, sum(net_usd) AS revenue,
                          sum(net_usd) / nullif(count(*) FILTER (WHERE status = 'COMPLETED'), 0) AS rev_per_appt, count(*) FILTER (WHERE status = 'CANCELLED') * 1.0 / count(*) AS cancel_rate
                   FROM gold.fact_spa_appointments WHERE {c.P()} AND {c.D('business_date')} AND {w} GROUP BY ALL ORDER BY revenue DESC""")
    byspa = c.df(f"""SELECT o.outlet_name AS spa, sum(a.duration_minutes) FILTER (WHERE a.status = 'COMPLETED') / 60.0 / (any_value(o.capacity) * 10 * {c.days}) AS utilisation, sum(a.net_usd) AS revenue,
                            count(*) FILTER (WHERE a.status = 'COMPLETED') AS appts
                     FROM gold.fact_spa_appointments a JOIN gold.dim_outlet o ON o.outlet_code = a.spa_code WHERE {c.P('a.property_id')} AND {c.D('a.business_date')} AND {w} GROUP BY 1 ORDER BY utilisation DESC""")
    th = c.df(f"""SELECT e.employee_name AS therapist, p.short_name AS property, count(*) FILTER (WHERE a.status = 'COMPLETED') AS appts,
                         sum(a.duration_minutes) FILTER (WHERE a.status = 'COMPLETED') / 60.0 / (10 * {c.days}) AS utilisation, sum(a.net_usd) AS revenue
                  FROM gold.fact_spa_appointments a JOIN gold.dim_employee e ON e.employee_id = a.therapist_id JOIN gold.dim_property p ON p.property_id = a.property_id
                  WHERE {c.P('a.property_id')} AND {c.D('a.business_date')} AND {w} GROUP BY ALL ORDER BY revenue DESC LIMIT 14""")
    hm = c.df(f"""SELECT strftime(business_date, '%a') AS dow, lpad(CAST(hour(start_ts) AS VARCHAR), 2, '0') || ':00' AS hour, count(*) * 1.0 / count(DISTINCT business_date) AS appts
                  FROM gold.fact_spa_appointments WHERE {c.P()} AND {c.D('business_date')} AND status = 'COMPLETED' GROUP BY ALL""")
    panels = [
        tline(c, "Appointment volume and forecast", [("Completed appointments", "actual", daily), ("Forecast", "forecast", fc)], "num", 7),
        bar("Service utilisation by spa", byspa, "spa", {"Therapist utilisation": "utilisation"}, "pct", 5, horizontal=True),
        bar("Service category performance", cat, "service_category", {"Revenue": "revenue"}, "usd", 5),
        table("Service performance", svc, [("service", "Service", "text"), ("category", "Category", "text"), ("appts", "Appointments", "num"), ("revenue", "Revenue", "usd"),
                                           ("rev_per_appt", "Revenue / appt", "usd0"), ("cancel_rate", "Cancel rate", "pct")], 7),
        table("Therapist utilisation", th, [("therapist", "Therapist", "text"), ("property", "Property", "text"), ("appts", "Appointments", "num"), ("utilisation", "Utilisation", "pct"), ("revenue", "Revenue", "usd")], 6),
        heat("Average completed appointments by day and start hour", hm, "hour", "dow", "appts", "dec1", 6, yorder=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
    ]
    low = byspa.iloc[-1] if len(byspa) else None
    insights = [movement(c, "Spa revenue", m["rev"], "usd"), movement(c, "Therapist utilisation", kpis[3]["value"] is not None and [kpis[3]["value"], kpis[3]["prev"]] or [None, None], "pct"),
                movement(c, "Cancellation rate", ratio(m, "cx", "n"), "pct", good="down"),
                {"text": f"{low['spa']} has the most unused capacity, with therapist utilisation at {low['utilisation']:.0%}.", "tone": "bad" if low["utilisation"] < 0.4 else "neutral", "measure": "Therapist utilisation"} if low is not None else None]
    return page(c, "Spa & Leisure", "Appointments, utilisation, service mix and therapist productivity", kpis, panels, insights, "Spa & Leisure")


# ----------------------------------------------------------------------------- marketing
def marketing(c: Ctx) -> dict:
    scope = f"({c.P()} OR property_id = 'ALL')" if c.scope.enterprise else c.P()
    m = {k: [] for k in ("spend", "rev", "clicks", "conv", "cust", "imp")}
    for prev in (False, True):
        row = c.con.execute(f"""SELECT sum(spend_usd), sum(attributed_revenue_usd), sum(clicks), sum(conversions), sum(new_customers), sum(impressions)
                                FROM gold.fact_marketing_campaigns WHERE {scope} AND {c.D('activity_date', prev)}""").fetchall()[0]
        for k, v in zip(m, row):
            m[k].append(None if v is None else float(v))
    loy = c.pair("gold.fact_customer_engagement", "event_date", {"earn": "sum(points) FILTER (WHERE event_type = 'EARN')", "red": "sum(points) FILTER (WHERE event_type = 'REDEEM')", "act": "count(DISTINCT loyalty_id)"})
    ret = []
    for prev in (False, True):
        s = c.cs if prev else c.start
        ret.append(c.val(f"""WITH cur AS (SELECT DISTINCT guest_key FROM gold.fact_reservations WHERE {c.P()} AND {c.D('arrival_date', prev)} AND status IN {OCC} AND guest_key IS NOT NULL)
                             SELECT avg((EXISTS (SELECT 1 FROM gold.fact_reservations h WHERE h.guest_key = cur.guest_key AND h.status IN {OCC} AND h.arrival_date < DATE '{s}'))::INT) FROM cur"""))
    g = c.df("""SELECT s.segment, count(*) AS guests, avg(s.clv_3yr_usd) AS clv, avg(s.churn_probability) AS churn, sum(s.annual_value_usd) AS annual_value, avg(g.verticals_used) AS verticals
                FROM ai.guest_scores s JOIN gold.mart_guest_360 g USING (guest_key) GROUP BY 1 ORDER BY clv DESC""")
    clv = c.val("SELECT avg(clv_3yr_usd) FROM ai.guest_scores")
    xs = c.val("SELECT avg((verticals_used >= 2)::INT) FROM gold.mart_guest_360 WHERE total_spend_usd > 0")
    kpis = [kpi(c, "Campaign spend", m["spend"], "usd", good="neutral"), kpi(c, "Customer acquisition cost", ratio(m, "spend", "cust"), "usd0", good="down", defn="Customer Acquisition Cost"),
            kpi(c, "Conversion rate", ratio(m, "conv", "clicks"), "pct2"), kpi(c, "Campaign ROI", [div((m["rev"][i] or 0) - (m["spend"][i] or 0), m["spend"][i]) for i in (0, 1)], "signed_pct", defn="Campaign ROI"),
            kpi(c, "Active loyalty members", loy["act"], "num"), kpi(c, "Points redemption rate", ratio(loy, "red", "earn"), "pct", good="neutral"),
            kpi(c, "Customer lifetime value (3-year)", [clv, None], "usd0"), kpi(c, "Guest retention (returning guests)", ret, "pct"), kpi(c, "Cross-selling conversion", [xs, None], "pct")]
    daily = c.df(f"SELECT activity_date AS d, sum(spend_usd) AS spend, sum(attributed_revenue_usd) AS rev FROM gold.fact_marketing_campaigns WHERE {scope} AND {c.D('activity_date')} GROUP BY 1")
    ch = c.df(f"""SELECT channel, sum(spend_usd) AS spend, sum(clicks) AS clicks, sum(conversions) AS conversions, sum(conversions) * 1.0 / nullif(sum(clicks), 0) AS cvr,
                         sum(spend_usd) / nullif(sum(new_customers), 0) AS cac, sum(attributed_revenue_usd) AS revenue, (sum(attributed_revenue_usd) - sum(spend_usd)) / sum(spend_usd) AS roi
                  FROM gold.fact_marketing_campaigns WHERE {scope} AND {c.D('activity_date')} GROUP BY 1 ORDER BY roi DESC""")
    camp = c.df(f"""SELECT d.campaign_name AS campaign, f.channel, f.target_vertical AS vertical, sum(f.spend_usd) AS spend, sum(f.conversions) AS conversions, sum(f.attributed_revenue_usd) AS revenue,
                           (sum(f.attributed_revenue_usd) - sum(f.spend_usd)) / sum(f.spend_usd) AS roi, any_value(s.predicted_conv_per_1k) AS predicted, sum(f.conversions) / sum(f.spend_usd) * 1000 AS actual_rate
                    FROM gold.fact_marketing_campaigns f JOIN gold.dim_campaign d USING (campaign_id) LEFT JOIN ai.campaign_scores s USING (campaign_id)
                    WHERE {scope.replace('property_id', 'f.property_id')} AND {c.D('f.activity_date')} GROUP BY ALL HAVING sum(f.spend_usd) > 500 ORDER BY revenue - spend DESC""")
    camp = pd.concat([camp.head(6), camp.tail(4)]).drop_duplicates() if len(camp) > 10 else camp
    vert = c.df("SELECT verticals_used || ' vertical' || CASE WHEN verticals_used > 1 THEN 's' ELSE '' END AS breadth, count(*) AS guests, avg(total_spend_usd) AS spend FROM gold.mart_guest_360 WHERE total_spend_usd > 0 GROUP BY verticals_used ORDER BY verticals_used")
    tier = c.df("SELECT loyalty_tier AS tier, count(*) AS guests, avg(total_spend_usd) AS avg_spend, avg(stays) AS stays, avg(avg_nps) AS nps FROM gold.mart_guest_360 WHERE total_spend_usd > 0 GROUP BY 1 ORDER BY avg_spend DESC")
    panels = [
        tline(c, "Campaign spend and attributed revenue", [("Attributed revenue", "actual", daily.rename(columns={"rev": "v"})[["d", "v"]]), ("Spend", "compare", daily.rename(columns={"spend": "v"})[["d", "v"]])], "usd", 7),
        bar("Campaign ROI by channel", ch, "channel", {"ROI": "roi"}, "signed_pct", 5, horizontal=True),
        table("Channel performance", ch, [("channel", "Channel", "text"), ("spend", "Spend", "usd"), ("clicks", "Clicks", "num"), ("conversions", "Conversions", "num"), ("cvr", "Conv. rate", "pct2"),
                                          ("cac", "CAC", "usd0"), ("revenue", "Attributed revenue", "usd"), ("roi", "ROI", "signed_pct")], 7),
        bar("Cross-business engagement: guests by verticals used", vert, "breadth", {"Guests": "guests"}, "num", 5, note="A guest who stays, dines and visits the spa counts as three verticals."),
        table("Best and worst campaigns", camp, [("campaign", "Campaign", "text"), ("spend", "Spend", "usd"), ("conversions", "Conv.", "num"), ("revenue", "Attributed revenue", "usd"), ("roi", "ROI", "signed_pct"),
                                                 ("actual_rate", "Conv. / $1k", "dec1"), ("predicted", "Predicted / $1k", "dec1")], 7, "Predicted conversions per $1,000 come from the Campaign Conversion model."),
        table("Guest segments, lifetime value and churn risk", g, [("segment", "Segment", "text"), ("guests", "Guests", "num"), ("clv", "CLV (3-year)", "usd0"), ("churn", "Churn risk", "pct"),
                                                                    ("verticals", "Verticals used", "dec1")], 5, "Enterprise-wide; from the Guest Segmentation and Churn models."),
        table("Loyalty programme engagement", tier, [("tier", "Tier", "text"), ("guests", "Guests", "num"), ("avg_spend", "Avg lifetime spend", "usd0"), ("stays", "Avg stays", "dec1"), ("nps", "Avg NPS", "dec1")], 6),
    ]
    best, worst = (ch.iloc[0], ch.iloc[-1]) if len(ch) > 1 else (None, None)
    insights = [movement(c, "Campaign ROI", kpis[3]["value"] is not None and [kpis[3]["value"], kpis[3]["prev"]] or [None, None], "pct"), movement(c, "Customer acquisition cost", ratio(m, "spend", "cust"), "usd0", good="down"),
                {"text": f"{best['channel']} returns {best['roi']:+.0%} while {worst['channel']} returns {worst['roi']:+.0%} on spend in the period.", "tone": "neutral", "measure": "Campaign ROI by channel"} if best is not None else None,
                {"text": f"{xs:.0%} of spending guests have used two or more business verticals.", "tone": "neutral", "measure": "Cross-selling conversion"} if xs else None]
    return page(c, "Marketing & Loyalty", "Campaign effectiveness, acquisition, loyalty and lifetime value", kpis, panels, insights, "Marketing")


# ----------------------------------------------------------------------------- finance
def finance(c: Ctx) -> dict:
    m = c.pair("gold.fact_financial_transactions", "posting_date", {"rev": "sum(amount_usd) FILTER (WHERE account_type = 'REVENUE')", "opex": "sum(amount_usd) FILTER (WHERE account_type = 'EXPENSE')"})
    avail = c.pair("gold.fact_room_occupancy", "business_date", {"a": "sum(rooms_available)"})["a"]
    gop = [(m["rev"][i] or 0) - (m["opex"][i] or 0) if m["rev"][i] is not None else None for i in (0, 1)]
    bud_o, bud_r = [c.budget("EXPENSE"), c.budget("EXPENSE", True)], [c.budget("REVENUE"), c.budget("REVENUE", True)]
    pr = c.pair("gold.fact_procurement", "po_date", {"spend": "sum(po_amount_usd)", "ot": "avg(on_time::INT)", "n": "count(*)"})
    kpis = [kpi(c, "Consolidated revenue", m["rev"], "usd"), kpi(c, "Operating expenditure", m["opex"], "usd", good="down"), kpi(c, "GOP", gop, "usd"),
            kpi(c, "GOP margin", [div(gop[0], m["rev"][0]), div(gop[1], m["rev"][1])], "pct"), kpi(c, "GOPPAR", [div(gop[0], avail[0]), div(gop[1], avail[1])], "usd0"),
            kpi(c, "Revenue vs budget", [div(m["rev"][i], bud_r[i]) - 1 if bud_r[i] else None for i in (0, 1)], "signed_pct"),
            kpi(c, "OPEX vs budget", [div(m["opex"][i], bud_o[i]) - 1 if bud_o[i] else None for i in (0, 1)], "signed_pct", good="down", defn="Budget Variance"),
            kpi(c, "Procurement spend", pr["spend"], "usd", good="neutral"), kpi(c, "Supplier on-time delivery", pr["ot"], "pct")]
    mon = c.df(f"""SELECT CAST(date_trunc('month', posting_date) AS DATE) AS d, sum(amount_usd) FILTER (WHERE account_type = 'REVENUE') AS rev, sum(amount_usd) FILTER (WHERE account_type = 'EXPENSE') AS opex
                   FROM gold.fact_financial_transactions WHERE {c.P()} AND posting_date >= date_trunc('month', DATE '{AS_OF}' - INTERVAL 12 MONTH) AND posting_date < date_trunc('month', DATE '{AS_OF}') GROUP BY 1""")
    bud = c.df(f"""SELECT period_start AS d, sum(budget_usd) FILTER (WHERE account_type = 'REVENUE') AS rev, sum(budget_usd) FILTER (WHERE account_type = 'EXPENSE') AS opex FROM gold.fact_budget
                   WHERE {c.P()} AND period_start >= date_trunc('month', DATE '{AS_OF}' - INTERVAL 12 MONTH) AND period_start <= date_trunc('month', DATE '{AS_OF}' + INTERVAL 2 MONTH) GROUP BY 1""")
    fcm = c.df(f"""WITH f AS (SELECT metric, business_date AS d, sum(forecast_value) AS v FROM gold.fact_forecasts WHERE forecast_type = 'forecast' AND metric IN ('finance_revenue', 'finance_opex') AND {c.P()} GROUP BY ALL),
                        a AS (SELECT posting_date AS d, sum(amount_usd) FILTER (WHERE account_type = 'REVENUE') AS rev, sum(amount_usd) FILTER (WHERE account_type = 'EXPENSE') AS opex
                              FROM gold.fact_financial_transactions WHERE {c.P()} AND posting_date >= date_trunc('month', DATE '{AS_OF}') GROUP BY 1)
                   SELECT CAST(date_trunc('month', d) AS DATE) AS d, sum(rev) AS rev, sum(opex) AS opex FROM (
                       SELECT d, rev, opex FROM a UNION ALL SELECT d, v, 0 FROM f WHERE metric = 'finance_revenue' UNION ALL SELECT d, 0, v FROM f WHERE metric = 'finance_opex')
                   WHERE d <= last_day(DATE '{AS_OF}' + INTERVAL 2 MONTH) GROUP BY 1""")
    cc = c.df(f"SELECT cost_centre, sum(amount_usd) AS actual FROM gold.fact_financial_transactions WHERE {c.P()} AND account_type = 'EXPENSE' AND {c.D('posting_date')} GROUP BY 1 ORDER BY 2 DESC")
    cc["budget"] = [c.budget("EXPENSE", extra=f"cost_centre = '{x}'") for x in cc["cost_centre"]]
    cc["variance"] = cc["actual"] - cc["budget"]
    cc["variance_pct"] = cc["actual"] / cc["budget"] - 1
    pl = c.df(f"""SELECT f.property_id, p.short_name AS property, sum(amount_usd) FILTER (WHERE account_type = 'REVENUE') AS revenue, sum(amount_usd) FILTER (WHERE account_type = 'EXPENSE') AS opex
                  FROM gold.fact_financial_transactions f JOIN gold.dim_property p USING (property_id) WHERE {c.P('f.property_id')} AND {c.D('posting_date')} GROUP BY ALL ORDER BY revenue DESC""")
    pl["gop"], pl["margin"] = pl["revenue"] - pl["opex"], (pl["revenue"] - pl["opex"]) / pl["revenue"]
    saved = c.props
    budp = []
    for pid in pl["property_id"]:
        c.props = [pid]
        budp.append(c.budget("EXPENSE"))
    c.props = saved
    pl["opex_var"] = pl["opex"] / pd.Series(budp, dtype=float) - 1
    pcat = c.df(f"SELECT category, sum(po_amount_usd) AS spend, count(*) AS orders FROM gold.fact_procurement WHERE {c.P()} AND {c.D('po_date')} GROUP BY 1 ORDER BY spend DESC")
    sup = c.df(f"""WITH b AS (SELECT item_description, median(unit_price_usd) AS med FROM gold.fact_procurement GROUP BY 1)
                   SELECT o.supplier_name AS supplier, o.category, sum(o.po_amount_usd) AS spend, count(*) AS orders, avg(o.on_time::INT) AS on_time, avg(o.unit_price_usd / b.med) - 1 AS price_index
                   FROM gold.fact_procurement o JOIN b USING (item_description) WHERE {c.P('o.property_id')} AND {c.D('o.po_date')} GROUP BY ALL ORDER BY spend DESC LIMIT 12""")
    rec = c.df(f"""SELECT r.fiscal_period AS period, p.short_name AS property, r.business_vertical AS vertical, r.operational_local AS operational, r.ledger_local AS ledger, r.difference_pct AS difference, r.status
                   FROM ops.reconciliation r JOIN gold.dim_property p USING (property_id) WHERE {c.P('r.property_id')} AND r.fiscal_period >= strftime(DATE '{AS_OF}' - INTERVAL 2 MONTH, '%Y-%m')
                   ORDER BY (r.status = 'Break') DESC, r.fiscal_period DESC, property LIMIT 12""")
    gd = c.df(f"SELECT business_date AS d, sum(gop_usd) AS v FROM gold.mart_executive_daily WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1")
    gfc = c.forecast("finance_gop") if c.live and c.grain == "day" else None
    panels = [
        tline(c, "Monthly revenue: actual, budget and forecast", [("Actual", "actual", mon.rename(columns={"rev": "v"})[["d", "v"]]), ("Budget", "budget", bud.rename(columns={"rev": "v"})[["d", "v"]]),
                                                                  ("Forecast (month to date actual plus forecast)", "forecast", fcm.rename(columns={"rev": "v"})[["d", "v"]])], "usd", 6, bucket=False),
        tline(c, "Monthly operating expenditure: actual, budget and forecast", [("Actual", "actual", mon.rename(columns={"opex": "v"})[["d", "v"]]), ("Budget", "budget", bud.rename(columns={"opex": "v"})[["d", "v"]]),
                                                                                ("Forecast (month to date actual plus forecast)", "forecast", fcm.rename(columns={"opex": "v"})[["d", "v"]])], "usd", 6, bucket=False),
        table("Cost-centre analysis: actual versus budget", cc, [("cost_centre", "Cost centre", "text"), ("actual", "Actual", "usd"), ("budget", "Budget", "usd"), ("variance", "Variance", "usd"),
                                                                  ("variance_pct", "Variance %", "signed_pct_bad")], 6),
        table("Property-level financial comparison", pl, [("property", "Property", "text"), ("revenue", "Revenue", "usd"), ("opex", "OPEX", "usd"), ("gop", "GOP", "usd"), ("margin", "GOP margin", "pct"),
                                                           ("opex_var", "OPEX vs budget", "signed_pct_bad")], 6, drill="finance"),
        tline(c, "Gross operating profit: actual and forecast", [("Actual", "actual", gd), ("Forecast", "forecast", gfc)], "usd", 6),
        bar("Procurement spend by category", pcat, "category", {"Spend": "spend"}, "usd", 6, horizontal=True),
        table("Supplier performance", sup, [("supplier", "Supplier", "text"), ("category", "Category", "text"), ("spend", "Spend", "usd"), ("orders", "Orders", "num"), ("on_time", "On time", "pct"),
                                            ("price_index", "Price vs benchmark", "signed_pct_bad")], 7, "Price versus the group median for the same items."),
        table("Revenue reconciliation: operations to ledger", rec, [("period", "Period", "text"), ("property", "Property", "text"), ("vertical", "Vertical", "text"), ("difference", "Difference", "signed_pct"),
                                                                     ("status", "Status", "tag")], 5, "Operational revenue in Gold against GL postings, local currency."),
    ]
    over = cc[cc["variance"] > 0].nlargest(1, "variance")
    insights = [movement(c, "Consolidated revenue", m["rev"], "usd"), movement(c, "GOP", gop, "usd"), movement(c, "Operating expenditure", m["opex"], "usd", good="down"),
                {"text": f"{over.iloc[0]['cost_centre']} is the largest overspend: {fmtv(over.iloc[0]['variance'], 'usd')} ({over.iloc[0]['variance_pct']:+.1%}) over the pro-rated budget.", "tone": "bad",
                 "measure": "Cost-centre analysis"} if len(over) else None,
                {"text": f"{int((rec['status'] == 'Break').sum())} revenue reconciliation break(s) in the last three periods need Finance review.", "tone": "bad", "measure": "Revenue reconciliation"} if (rec["status"] == "Break").any() else None]
    return page(c, "Finance", "Consolidated revenue, cost, profitability, budget and procurement", kpis, panels, insights, "Finance")


DASHBOARDS = {  # name -> (builder, required vertical or None)
    "executive": (executive, None), "hotels": (hotels, "Hotels"), "restaurants": (restaurants, "Restaurants"), "waterpark": (waterpark, "Waterpark"), "retail": (retail, "Retail"),
    "spa": (spa, "Spa & Leisure"), "marketing": (marketing, "Marketing"), "finance": (finance, "Finance"),
}
