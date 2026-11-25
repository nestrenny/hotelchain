"""CxO morning report: the thirteen questions the executive team asks every morning, each answered by
the KPIs that measure it, a verdict and the business action it points to.

Every answer is computed for the selected properties and period against the comparison period, from
the same Gold tables as the domain dashboards. "Last night" is the last day of the period, and "next
month" is the calendar month after the business date, read from the forecasts.

The verdict and the suggested action come from transparent rules written below, not from a model:
a KPI moving the right way by more than a small tolerance counts for the answer, the wrong way
against it, and the primary KPI of each question carries double weight.
"""
from __future__ import annotations

from datetime import date, timedelta

from hip.api.dashboards import Ctx, div, page
from hip.settings import AS_OF

TOL = {"pct": 0.005, "rel": 0.02}          # changes smaller than this count as flat: half a point for rates, 2% otherwise
VERDICT = {"good": "On track", "watch": "Watch", "bad": "Act now"}
# Management targets. A KPI outside its target holds the verdict at Watch at best. Illustrative values for the
# prototype; in production they come from the approved budget and brand standards.
TARGETS = {"Food cost %": ("max", 0.35), "Spa utilisation": ("min", 0.50), "Payroll cost %": ("max", 0.35), "Utilities cost %": ("max", 0.05), "GOP margin": ("min", 0.30)}
DOMAINS = {"Hotels", "Restaurants", "Waterpark", "Spa & Leisure", "Retail", "Marketing", "Finance"}   # a role needs at least one to see the page


def k(c: Ctx, label: str, pair, fmt: str, good: str = "up", defn: str | None = None, prev_label: str | None = None) -> dict:
    out = {"label": label, "value": pair[0], "prev": pair[1], "fmt": fmt, "good": good, "definition": c.defs.get(defn or label), "prev_label": prev_label}
    if label in TARGETS:
        side, v = TARGETS[label]
        out["target"] = {"side": side, "value": v, "met": None if pair[0] is None else (pair[0] <= v if side == "max" else pair[0] >= v)}
    return out


def off_target(kpis: list[dict]) -> bool:
    return any(x.get("target", {}).get("met") is False for x in kpis)


def sig(x: dict) -> int:
    """+1 when a KPI moved the favourable way by more than the tolerance, -1 the unfavourable way, 0 otherwise."""
    v, p = x["value"], x["prev"]
    if v is None or p is None or x["good"] == "neutral":
        return 0
    if "pct" in x["fmt"]:
        d = v - p
        if abs(d) < TOL["pct"]:
            return 0
    else:
        if not p:
            return 0
        d = v / p - 1
        if abs(d) < TOL["rel"]:
            return 0
    return 1 if (d > 0) == (x["good"] == "up") else -1


def change(x: dict) -> str:
    v, p = x["value"], x["prev"]
    if v is None or p is None:
        return ""
    if "pct" in x["fmt"]:
        return f"{(v - p) * 100:+.1f} pts"
    return f"{(v / p - 1):+.1%}" if p else ""


def show(v, fmt: str) -> str:
    if v is None:
        return "n/a"
    if fmt == "usd":
        return f"${v / 1e6:,.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}K" if abs(v) >= 1e4 else f"${v:,.0f}"
    if fmt in ("usd0", "usd1"):
        return f"${v:,.0f}"
    if fmt in ("pct", "pct2"):
        return f"{v:.1%}"
    if fmt == "dec1x":
        return f"{v:.1f}x"
    if fmt == "dec1":
        return f"{v:,.1f}"
    return f"{v:,.0f}"


def verdict(kpis: list[dict], breach: bool = False) -> str:
    s = [sig(x) for x in kpis]
    score = 2 * s[0] + sum(s[1:]) if s else 0
    if breach and score > 0:
        return "watch"
    if score <= -2 or (breach and score < 0):
        return "bad"
    if score >= 2:
        return "good"
    return "watch"


def q(n: int, question: str, domain: str, kpis: list[dict], read: str, action: str, link: str, breach: bool = False, vd: str | None = None) -> dict:
    breach = breach or off_target(kpis)
    v = vd or verdict(kpis, breach)
    return {"n": n, "question": question, "domain": domain, "verdict": v, "verdict_label": VERDICT[v], "kpis": kpis, "read": read, "action": action, "link": link}


def worst(c: Ctx, sql: str, col: str, highest: bool = True) -> tuple[str, float] | None:
    """The property with the highest (or lowest) value of a per-property measure, when more than one is in view."""
    df = c.df(sql).dropna(subset=[col])
    if len(df) < 2:
        return None
    r = df.sort_values(col, ascending=not highest).iloc[0]
    return c.names[r["property_id"]], float(r[col])


# ----------------------------------------------------------------------------- the report
def morning(c: Ctx) -> dict:
    sees = lambda v: v in c.scope.verticals  # noqa: E731
    cmp_ = c.clabel
    items: list[dict] = []
    ex = c.pair("gold.mart_executive_daily", "business_date", {
        "sold": "sum(rooms_sold)", "avail": "sum(rooms_available)", "room": "sum(room_revenue_usd)", "rev": "sum(total_revenue_usd)",
        "glrev": "sum(gl_revenue_usd)", "opex": "sum(opex_usd)", "gop": "sum(gop_usd)"})
    occ = [div(ex["sold"][i], ex["avail"][i]) for i in (0, 1)]
    adr = [div(ex["room"][i], ex["sold"][i]) for i in (0, 1)]
    revpar = [div(ex["room"][i], ex["avail"][i]) for i in (0, 1)]
    trevpar = [div(ex["rev"][i], ex["avail"][i]) for i in (0, 1)]

    if sees("Hotels"):
        # 1 -- how full is the hotel today
        night = c.end
        ly = night - timedelta(days=364)
        n = {d: c.val(f"SELECT sum(rooms_sold) / sum(rooms_available) FROM gold.mart_executive_daily WHERE {c.P()} AND business_date = DATE '{d}'") for d in (night, ly)}
        label = "Occupancy last night" if night == AS_OF else f"Occupancy on {night:%d %b}"
        k1 = [k(c, label, [n[night], n[ly]], "pct", defn="Occupancy %", prev_label="same night last year"), k(c, "Occupancy, period", occ, "pct", defn="Occupancy %")]
        low = worst(c, f"SELECT property_id, sum(rooms_sold) / sum(rooms_available) AS v FROM gold.mart_executive_daily WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1", "v", False)
        s_night, s_per = sig(k1[0]), sig(k1[1])
        if s_night < 0 and s_per >= 0:
            act = "Last night was softer than a year ago although the period is ahead: check the pickup for the next 14 nights and open offers only where it is behind."
        elif s_per < 0:
            act = ("Rate is holding while rooms go unsold: open targeted offers for the soft nights in the 60-day pickup." if sig(k(c, "", adr, "usd0")) >= 0
                   else "Demand is weak at every price: step up direct and loyalty marketing, and review OTA and wholesale allocations.")
        elif s_per > 0:
            act = "Demand is strong: test higher rates on the nights already close to full."
        else:
            act = "Occupancy is steady: protect rate and watch pickup for the next 30 nights."
        items.append(q(1, "How full is my hotel today?", "Hotels", k1,
                       f"{show(n[night], 'pct')} of sellable rooms were occupied {'last night' if night == AS_OF else 'on ' + format(night, '%d %B')} "
                       f"({change(k1[0]) or 'no comparison'} on the same night last year); {show(occ[0], 'pct')} across the period." + (f" {low[0]} is the least full at {low[1]:.1%}." if low else ""),
                       act, "#/hotels"))

        # 2 -- right price
        k2 = [k(c, "ADR", adr, "usd0"), k(c, "Occupancy", occ, "pct", defn="Occupancy %")]
        a, o = sig(k2[0]), sig(k2[1])
        if a > 0 and o >= 0:
            act, vd = "Price increases are sticking without losing volume: keep testing rate on high-demand dates.", "good"
        elif a > 0 > o:
            act, vd = "Rate is up but rooms are being lost: check whether the increase is pricing guests out on shoulder nights.", "watch"
        elif a < 0 < o:
            act, vd = "Discounting is buying occupancy: check which channels and segments are diluting rate.", "watch"
        elif a < 0:
            act, vd = "Rate and occupancy are both falling: review pricing against the competitive set and the event calendar.", "bad"
        else:
            act, vd = "Rate is steady: look for dates where demand would carry a higher price.", "watch" if o < 0 else "good"
        items.append(q(2, "Am I selling rooms at the right price?", "Hotels", k2,
                       f"The average room sold for {show(adr[0], 'usd0')} ({change(k2[0]) or 'no comparison'} on the {cmp_}), with occupancy {change(k2[1]) or 'unchanged'}.", act, "#/hotels", vd=vd))

        # 3 -- room revenue
        k3 = [k(c, "RevPAR", revpar, "usd0"), k(c, "Room revenue", [ex["room"][0], ex["room"][1]], "usd")]
        driver = "rate" if abs(sig(k2[0])) >= abs(sig(k2[1])) and sig(k2[0]) else "occupancy" if sig(k2[1]) else None
        act = (f"RevPAR is falling, mostly through {driver}: act on the {'pricing' if driver == 'rate' else 'demand'} question above first." if sig(k3[0]) < 0 and driver
               else "RevPAR is growing: keep the current pricing and channel strategy." if sig(k3[0]) > 0 else "RevPAR is flat: the gain has to come from rate on peak nights or occupancy on soft ones.")
        items.append(q(3, "Am I generating enough room revenue?", "Hotels", k3,
                       f"Each available room earned {show(revpar[0], 'usd0')} ({change(k3[0]) or 'no comparison'}), {show(ex['room'][0], 'usd')} of room revenue in total.", act, "#/hotels"))

        # 4 -- total revenue per room
        share = [div(ex["rev"][i] - ex["room"][i], ex["rev"][i]) if ex["rev"][i] else None for i in (0, 1)]
        k4 = [k(c, "TRevPAR", trevpar, "usd0"), k(c, "Non-room share of revenue", share, "pct")]
        act = ("Guests are spending less outside the room: promote restaurants, spa and waterpark at booking and check-in." if sig(k4[1]) < 0
               else "Outlets are adding more per guest: keep cross-selling packages." if sig(k4[1]) > 0 else "Spend outside the room is steady: bundle dining and spa offers into direct packages to lift it.")
        items.append(q(4, "How much revenue are my guests generating overall?", "Hotels", k4,
                       f"Each available room brought in {show(trevpar[0], 'usd0')} across every outlet, {show(share[0], 'pct')} of it outside the room.", act, "#/executive"))

    if sees("Restaurants"):
        r = c.pair("gold.fact_restaurant_covers", "business_date", {"cov": "sum(covers)", "net": "sum(net_usd)", "fc": "sum(food_cost_usd)"})
        fcp = [div(r["fc"][i], r["net"][i]) for i in (0, 1)]
        k5 = [k(c, "Covers", r["cov"], "num"), k(c, "Average spend", [div(r["net"][i], r["cov"][i]) for i in (0, 1)], "usd1", defn="Spend per Guest"),
              k(c, "Food cost %", fcp, "pct", good="down", defn="Food Cost %")]
        hi = worst(c, f"SELECT property_id, sum(food_cost_usd) / nullif(sum(net_usd), 0) AS v FROM gold.fact_restaurant_covers WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1", "v")
        breach = (fcp[0] or 0) > TARGETS["Food cost %"][1]
        act = (f"Food cost is above the {TARGETS['Food cost %'][1]:.0%} target: review supplier prices, recipe costs and portion control{', starting with ' + hi[0] if hi else ''}." if breach
               else "Fewer guests are dining: promote the restaurants to in-house guests and open table bookings to non-residents." if sig(k5[0]) < 0
               else "Guests are spending less per visit: refresh the menu's high-margin dishes and train upselling." if sig(k5[1]) < 0
               else "Restaurants are performing: keep the menu engineering focus on high-margin best sellers.")
        items.append(q(5, "Are restaurants performing well?", "Restaurants", k5,
                       f"{show(r['cov'][0], 'num')} covers at {show(k5[1]['value'], 'usd1')} a head, with food cost at {show(fcp[0], 'pct')} of sales." + (f" {hi[0]} runs the highest food cost at {hi[1]:.1%}." if hi else ""),
                       act, "#/restaurants"))

    if sees("Waterpark"):
        w = c.pair("gold.fact_waterpark_daily", "business_date", {"vis": "sum(visitors)", "cap": "sum(daily_capacity)", "busy": "count(*) FILTER (WHERE capacity_utilisation >= 0.9)"})
        if w["cap"][0]:
            k6 = [k(c, "Attendance", w["vis"], "num"), k(c, "Capacity utilisation", [div(w["vis"][i], w["cap"][i]) for i in (0, 1)], "pct", defn="Capacity Utilisation")]
            busy = int(w["busy"][0] or 0)
            act = (f"{busy} park-day{'s' if busy != 1 else ''} ran at 90% of capacity or more: switch to timed entry and add lifeguards and ride operators on peak days." if busy
                   else "Attendance is falling: run hotel-guest and twilight ticket offers on weekdays." if sig(k6[0]) < 0
                   else "The parks have headroom: market day passes to resort guests and local residents.")
            items.append(q(6, "Is my waterpark operating efficiently?", "Waterpark", k6,
                           f"{show(w['vis'][0], 'num')} visitors used {show(k6[1]['value'], 'pct')} of licensed capacity.", act, "#/waterpark", breach=busy > 0))

    if sees("Spa & Leisure"):
        sp = c.pair("gold.fact_spa_appointments", "business_date", {"rev": "sum(net_usd) FILTER (WHERE status = 'COMPLETED')",
                                                                     "hrs": "sum(duration_minutes) FILTER (WHERE status = 'COMPLETED') / 60.0"})
        ther = c.val(f"SELECT sum(capacity) FROM gold.dim_outlet WHERE outlet_type = 'Spa' AND {c.P()}") or 0
        if ther:
            util = [div(sp["hrs"][i], ther * 10 * c.days) for i in (0, 1)]
            k7 = [k(c, "Spa utilisation", util, "pct", defn="Spa Utilisation"), k(c, "Revenue per treatment hour", [div(sp["rev"][i], sp["hrs"][i]) for i in (0, 1)], "usd0")]
            act = ("Therapist time is going unsold: offer quiet-hour treatments to in-house guests and bundle them into packages." if (util[0] or 0) < TARGETS["Spa utilisation"][1]
                   else "Each hour is earning less: promote longer signature treatments over short express ones." if sig(k7[1]) < 0
                   else "The spa is busy and earning well: consider adding therapist shifts at the peak hours.")
            items.append(q(7, "Is my spa generating sufficient revenue?", "Spa & Leisure", k7,
                           f"Therapists were booked {show(util[0], 'pct')} of their available hours, earning {show(k7[1]['value'], 'usd0')} per treatment hour.", act, "#/spa"))

    if sees("Retail"):
        rt = c.pair("gold.fact_retail_sales", "business_date", {"net": "sum(net_usd)", "cost": "sum(cost_usd)"})
        inv = c.pair("gold.fact_inventory", "snapshot_date", {"val": "sum(stock_value_usd) / count(DISTINCT snapshot_date)"})
        if rt["net"][0]:
            k8 = [k(c, "Gross margin", [div((rt["net"][i] or 0) - (rt["cost"][i] or 0), rt["net"][i]) for i in (0, 1)], "pct"),
                  k(c, "Inventory turnover", [div(rt["cost"][i] * 365 / c.days, inv["val"][i]) if rt["cost"][i] else None for i in (0, 1)], "dec1x")]
            slow = c.val(f"SELECT sum(stock_value_usd) FROM ai.inventory_risk WHERE slow_moving AND {c.P()}") or 0
            act = ("Margin is slipping: check discounting against approved promotions." if sig(k8[0]) < 0
                   else "Stock is turning more slowly: cut reorder quantities on the lines with the most days of cover." if sig(k8[1]) < 0
                   else f"Clear the {show(slow, 'usd')} tied up in slow-moving lines with targeted promotions, and pause their reorders." if slow > 25000
                   else "Retail is profitable and stock is moving: keep the range and replenishment as they are.")
            items.append(q(8, "Is my retail business profitable?", "Retail", k8,
                           f"Retail kept {show(k8[0]['value'], 'pct')} of sales as gross margin, and stock turns {show(k8[1]['value'], 'dec1x')} a year." +
                           (f" {show(slow, 'usd')} is tied up in slow-moving lines." if slow else ""), act, "#/retail"))

    if sees("Hotels"):
        # 9 -- guest satisfaction
        g = c.pair("gold.fact_guest_feedback", "survey_date", {
            "nps": "(count(*) FILTER (WHERE nps_score >= 9) - count(*) FILTER (WHERE nps_score <= 6)) * 100.0 / nullif(count(*), 0)",
            "gss": "avg((overall_satisfaction >= 4)::INT)"})
        cp = c.pair("gold.fact_guest_complaints", "logged_date", {"n": "count(*)"})
        st = c.pair("gold.fact_reservations", "departure_date", {"n": "count(*)"}, "status = 'CHECKED_OUT'")
        rate = [div(cp["n"][i] * 100, st["n"][i]) if cp["n"][i] is not None else None for i in (0, 1)]
        k9 = [k(c, "NPS", g["nps"], "dec0"), k(c, "GSS", g["gss"], "pct"), k(c, "Complaints per 100 stays", rate, "dec1", good="down", defn="Complaint Rate")]
        top = c.df(f"""WITH cur AS (SELECT property_id, category, count(*) AS n FROM gold.fact_guest_complaints WHERE {c.P()} AND {c.D('logged_date')} GROUP BY ALL),
                            prv AS (SELECT property_id, category, count(*) AS p FROM gold.fact_guest_complaints WHERE {c.P()} AND {c.D('logged_date', True)} GROUP BY ALL)
                       SELECT cur.property_id, cur.category, n, coalesce(p, 0) AS p FROM cur LEFT JOIN prv USING (property_id, category) ORDER BY n - coalesce(p, 0) DESC LIMIT 1""")
        rise = top.iloc[0] if len(top) and top.iloc[0]["n"] - top.iloc[0]["p"] >= 5 else None
        act = (f"Complaints about '{rise['category']}' at {c.names[rise['property_id']]} rose from {int(rise['p'])} to {int(rise['n'])}: ask the general manager for a root-cause review this week."
               if rise is not None else "Satisfaction is slipping: review the lowest-scoring survey comments with each general manager." if sig(k9[0]) < 0 or sig(k9[1]) < 0
               else "Guests are satisfied: share the top-rated practices across properties.")
        items.append(q(9, "Are guests satisfied?", "Hotels", k9,
                       f"NPS is {show(g['nps'][0], 'num')}, {show(g['gss'][0], 'pct')} of surveyed guests rated their stay 4 or 5 out of 5, and guest relations logged "
                       f"{show(rate[0], 'dec1')} complaints per 100 stays.", act, "#/hotels", breach=rise is not None))

    if sees("Marketing"):
        scope = f"({c.P()} OR property_id = 'ALL')" if c.scope.enterprise else c.P()
        mk = {key: [] for key in ("spend", "attr", "new")}
        for prev in (False, True):
            row = c.con.execute(f"SELECT sum(spend_usd), sum(attributed_revenue_usd), sum(new_customers) FROM gold.fact_marketing_campaigns WHERE {scope} AND {c.D('activity_date', prev)}").fetchall()[0]
            for key, v in zip(mk, row):
                mk[key].append(None if v is None else float(v))
        if mk["spend"][0]:
            k10 = [k(c, "Marketing ROI", [div(mk["attr"][i] - mk["spend"][i], mk["spend"][i]) if mk["spend"][i] else None for i in (0, 1)], "pct", defn="Campaign ROI"),
                   k(c, "ROAS", [div(mk["attr"][i], mk["spend"][i]) for i in (0, 1)], "dec1x"),
                   k(c, "CAC", [div(mk["spend"][i], mk["new"][i]) for i in (0, 1)], "usd0", good="down", defn="Customer Acquisition Cost")]
            ch = c.df(f"""SELECT channel, (sum(attributed_revenue_usd) - sum(spend_usd)) / sum(spend_usd) AS roi FROM gold.fact_marketing_campaigns
                          WHERE {scope} AND {c.D('activity_date')} GROUP BY 1 HAVING sum(spend_usd) > 0 ORDER BY roi""")
            losing = ch[ch["roi"] < 0]
            act = (f"{', '.join(losing['channel'])} lost money: move that budget to {ch.iloc[-1]['channel']}, the best-returning channel." if len(losing)
                   else f"Every channel paid back: shift more budget towards {ch.iloc[-1]['channel']}." if len(ch) else "Review channel returns on the Marketing page.")
            items.append(q(10, "Are marketing campaigns effective?", "Marketing", k10,
                           f"Each dollar spent brought back {show(k10[1]['value'], 'dec1x').rstrip('x')} dollars of attributed revenue; a new customer cost {show(k10[2]['value'], 'usd0')}.",
                           act, "#/marketing", breach=len(losing) > 0))

    if sees("Finance"):
        # 11 -- operating costs
        fin = c.pair("gold.fact_financial_transactions", "posting_date", {
            "rev": "sum(amount_usd) FILTER (WHERE account_type = 'REVENUE')", "pay": "sum(amount_usd) FILTER (WHERE gl_account = '5000')",
            "util": "sum(amount_usd) FILTER (WHERE gl_account = '5400')", "rooms": "sum(amount_usd) FILTER (WHERE account_type = 'EXPENSE' AND cost_centre = 'Rooms')"})
        payp = [div(fin["pay"][i], fin["rev"][i]) for i in (0, 1)]
        utilp = [div(fin["util"][i], fin["rev"][i]) for i in (0, 1)]
        k11 = [k(c, "CPOR", [div(fin["rooms"][i], ex["sold"][i]) for i in (0, 1)], "usd0", good="down"),
               k(c, "Payroll cost %", payp, "pct", good="down"), k(c, "Utilities cost %", utilp, "pct", good="down")]
        bud = c.budget("EXPENSE")
        over = div(ex["opex"][0], bud) - 1 if bud else None
        hot = worst(c, f"""SELECT property_id, sum(amount_usd) FILTER (WHERE gl_account = '5400') / nullif(sum(amount_usd) FILTER (WHERE account_type = 'REVENUE'), 0) AS v
                           FROM gold.fact_financial_transactions WHERE {c.P()} AND {c.D('posting_date')} GROUP BY 1""", "v")
        act = ("Payroll is taking a bigger share of revenue: align rosters with the occupancy forecast for the next four weeks." if sig(k11[1]) < 0 or k11[1]["target"]["met"] is False
               else f"Utilities are rising faster than revenue{' at ' + hot[0] if hot else ''}: see Energy & Assets for the cause." if sig(k11[2]) < 0
               else "Rooms cost per occupied room is rising: review housekeeping productivity and amenity spend." if sig(k11[0]) < 0
               else "Costs are under control: keep labour scheduling tied to forecast demand.")
        items.append(q(11, "Am I controlling operating costs?", "Finance", k11,
                       f"Each occupied room cost {show(k11[0]['value'], 'usd0')} to service; payroll took {show(payp[0], 'pct')} of revenue and energy and water {show(utilp[0], 'pct')}." +
                       (f" Operating cost is {abs(over):.1%} {'over' if over > 0 else 'under'} budget." if over is not None else ""), act, "#/finance",
                       breach=over is not None and over > 0.02))

        # 12 -- profitability
        gm = [div(ex["gop"][i], ex["glrev"][i]) for i in (0, 1)]
        k12 = [k(c, "GOP", ex["gop"], "usd"), k(c, "GOP margin", gm, "pct", defn="GOP Margin"), k(c, "GOPPAR", [div(ex["gop"][i], ex["avail"][i]) for i in (0, 1)], "usd0")]
        bgop = (c.budget("REVENUE") or 0) - (bud or 0) if bud else None
        vs_b = div(ex["gop"][0] - bgop, abs(bgop)) if bgop else None
        weak = worst(c, f"SELECT property_id, sum(gop_usd) / nullif(sum(gl_revenue_usd), 0) AS v FROM gold.mart_executive_daily WHERE {c.P()} AND {c.D('business_date')} GROUP BY 1", "v", False)
        act = (f"{weak[0]} has the weakest margin at {weak[1]:.1%}: ask for a cost and pricing recovery plan." if weak and weak[1] < 0.2
               else "Profit is falling: start with the cost question above and the pricing question for rooms." if sig(k12[0]) < 0
               else (f"Profit is growing but {abs(vs_b):.1%} short of budget: ask Finance which properties and cost centres explain the gap"
                     + (f", starting with {weak[0]} ({weak[1]:.1%} margin)." if weak else ".")) if vs_b is not None and vs_b < -0.03
               else "The estate is profitable and on budget: protect margin by holding rate and labour productivity.")
        items.append(q(12, "Is the hotel profitable?", "Finance", k12,
                       f"Gross operating profit was {show(ex['gop'][0], 'usd')}, a {show(gm[0], 'pct')} margin" + (f", {abs(vs_b):.1%} {'above' if vs_b > 0 else 'below'} budget." if vs_b is not None else "."),
                       act, "#/finance", breach=vs_b is not None and vs_b < -0.03))

    # 13 -- next month
    nxt = (AS_OF.replace(day=1) + timedelta(days=32)).replace(day=1)
    nend = (nxt + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    ly0, ly1 = nxt.replace(year=nxt.year - 1), nend.replace(year=nend.year - 1)
    if sees("Hotels") or sees("Finance"):
        def f(metric: str, col: str = "forecast_value", extra: str = ""):
            return c.val(f"""SELECT sum({col}) FROM gold.fact_forecasts WHERE metric = '{metric}' AND forecast_type = 'forecast' AND {c.P()}
                             AND business_date BETWEEN DATE '{nxt}' AND DATE '{nend}' {extra}""")
        last = c.con.execute(f"""SELECT sum(rooms_sold), sum(rooms_available), sum(room_revenue_usd), sum(total_revenue_usd), sum(gop_usd) FROM gold.mart_executive_daily
                                 WHERE {c.P()} AND business_date BETWEEN DATE '{ly0}' AND DATE '{ly1}'""").fetchall()[0]
        rooms, cap, rrev = f("hotel_rooms_sold", extra="AND entity_id = 'ALL'"), f("hotel_rooms_sold", "capacity", "AND entity_id = 'ALL'"), f("hotel_room_revenue")
        lbl = f"{ly0:%B %Y}"
        k13 = []
        short = {"Forecast occupancy": "occupancy", "Forecast ADR": "ADR", "Forecast revenue": "revenue", "Forecast GOP": "GOP"}
        if sees("Hotels"):
            k13 += [k(c, "Forecast occupancy", [div(rooms, cap), div(last[0], last[1])], "pct", defn="Occupancy %", prev_label=lbl),
                    k(c, "Forecast ADR", [div(rrev, rooms), div(last[2], last[0])], "usd0", defn="ADR", prev_label=lbl)]
        if sees("Finance"):
            k13 += [k(c, "Forecast revenue", [f("finance_revenue"), last[3]], "usd", defn="Total revenue", prev_label=lbl),
                    k(c, "Forecast GOP", [f("finance_gop"), last[4]], "usd", defn="GOP", prev_label=lbl)]
        if k13 and k13[0]["value"] is not None:
            by = {x["label"]: sig(x) for x in k13}
            top_line = by.get("Forecast revenue", by.get("Forecast occupancy", 0))
            profit = by.get("Forecast GOP", top_line)
            s = profit if profit else top_line
            word = "better than" if s > 0 else "worse than" if s < 0 else "about the same as"
            if top_line >= 0 and profit < 0:
                act = f"Demand is expected to hold but profit to fall: costs are forecast to grow faster than revenue, so review the {nxt:%B} cost plan and rosters now."
            elif top_line < 0:
                act = "Demand is expected to be softer than last year: firm up group and corporate business now and plan lighter rosters for the quiet weeks."
            elif top_line > 0:
                act = "The outlook is stronger than last year: hold back discount allocations and plan staffing for the busy dates."
            else:
                act = "The outlook matches last year: focus on converting the remaining pickup at a good rate."
            items.append(q(13, "Will next month be better or worse?", "Forecast", k13,
                           f"{nxt:%B %Y} is forecast to be {word} {lbl}: " + ", ".join(f"{short[x['label']]} {show(x['value'], x['fmt'])} ({change(x) or 'n/a'})" for x in k13) + ".",
                           act, "#/forecasting"))

    counts = {v: sum(1 for i in items if i["verdict"] == v) for v in VERDICT}
    insights = [{"text": f"{len(items)} questions answered: {counts['good']} on track, {counts['watch']} to watch, {counts['bad']} needing action.", "tone": "bad" if counts["bad"] else "neutral",
                 "measure": "Morning report"}]
    insights += [{"text": f"Act now: {i['question']} {i['action']}", "tone": "bad", "measure": ", ".join(x["label"] for x in i["kpis"])} for i in items if i["verdict"] == "bad"]
    panel = {"type": "questions", "title": "The thirteen morning questions", "span": 12, "items": items,
             "note": "Each verdict compares the period with the " + cmp_ + ". The first KPI on each card carries double weight; changes of under half a point (rates) or 2% (amounts) count as flat."}
    spec = page(c, "CxO Morning Report", "Every KPI answers a management question and points to a business action", [], [panel], insights, None)
    spec["counts"] = counts
    spec["next_month"] = {"start": nxt, "end": nend, "compare_start": ly0, "compare_end": ly1}
    return spec
