"""Build the offline Power BI report in powerbi/ from the local lakehouse.

It writes a Power BI Project (.pbip) that Power BI Desktop opens directly:
  powerbi/data/*.parquet                         the data, exported from the Gold and AI layers
  powerbi/<name>.SemanticModel/                  tables, relationships and measures (TMDL)
  powerbi/<name>.Report/                         nine report pages (PBIR)
  powerbi/model.json                             the same tables and relationships, for tools/build_schema_diagram.py

Nothing is read from a server: the report loads the Parquet files beside it.

    python tools/build_powerbi.py               # export data and write the project
    python tools/build_powerbi.py --validate    # also check the report files against Microsoft's published schemas
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hip.settings import AS_OF  # noqa: E402

NAME = "Aurelian Hospitality Intelligence"
OUT = ROOT / "powerbi"
DATA = OUT / "data"
SM = OUT / f"{NAME}.SemanticModel"
RP = OUT / f"{NAME}.Report"
SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/"
SCHEMA_CACHE = ROOT / "data" / "docs_build" / "pbir_schemas"
V_VISUAL, V_PAGE, V_PAGES, V_REPORT = "2.4.0", "2.1.0", "1.1.0", "3.2.0"
NAVY, GOLD = "#0F2233", "#B8894A"
PALETTE = ["#1F5FA8", "#B8894A", "#2E8B6F", "#C0504D", "#7A5CA8", "#4BACC6", "#8C8C8C", "#E0A93B"]

WORDS = {"usd": "USD", "id": "ID", "kwh": "kWh", "kw": "kW", "gop": "GOP", "gl": "GL", "m3": "m3", "sku": "SKU", "t": "(t)"}


# Base columns whose natural name is wanted for a measure: Power BI does not allow both in one table.
BASE = {"rooms_sold", "rooms_available", "covers", "checks", "visitors", "conversions", "clicks", "room_nights"}


def friendly(col: str) -> str:
    return " ".join(WORDS.get(w, w.capitalize()) for w in col.split("_")) + (" (base)" if col in BASE else "")


# --------------------------------------------------------------------------------------------- tables
# (model table, file, kind, SQL).  Columns named date / property_id / business_vertical / outlet_code /
# room_type / channel / cost_centre are the keys the relationships are built from.
A = f"DATE '{AS_OF.isoformat()}'"
TABLES: list[tuple[str, str, str, str]] = [
    ("Date", "dim_date", "dim", f"""SELECT date, year, quarter, month, month_name, year_month, iso_week, day_name, is_weekend, date <= {A} AS is_actual
                                     FROM gold.dim_date ORDER BY date"""),
    ("Property", "dim_property", "dim", """SELECT property_id, short_name AS property, property_name, brand_name, region, country, city, currency, total_rooms, has_waterpark
                                           FROM gold.dim_property ORDER BY property_id"""),
    ("Business Vertical", "dim_business_vertical", "dim", "SELECT business_vertical, sort_order FROM gold.dim_business_vertical ORDER BY sort_order"),
    ("Outlet", "dim_outlet", "dim", "SELECT outlet_code, outlet_name, outlet_type, sub_type, capacity, property_id FROM gold.dim_outlet ORDER BY outlet_code"),
    ("Room Type", "dim_room", "dim", "SELECT room_type, room_type_name FROM gold.dim_room ORDER BY room_type"),
    ("Channel", "dim_channel", "dim", "SELECT channel, channel_type, is_direct FROM gold.dim_channel ORDER BY channel"),
    ("Cost Centre", "dim_cost_centre", "dim", """SELECT DISTINCT cost_centre FROM (SELECT cost_centre FROM gold.fact_financial_transactions UNION SELECT cost_centre FROM gold.fact_budget)
                                                 WHERE cost_centre IS NOT NULL ORDER BY 1"""),
    ("Executive Daily", "executive_daily", "fact", """SELECT business_date AS date, property_id, rooms_available::BIGINT AS rooms_available, rooms_sold::BIGINT AS rooms_sold, room_revenue_usd,
                                                      restaurant_revenue_usd, waterpark_revenue_usd, retail_revenue_usd, spa_revenue_usd, total_revenue_usd, gl_revenue_usd, opex_usd, gop_usd
                                                      FROM gold.mart_executive_daily"""),
    ("Revenue by Vertical", "revenue_by_vertical", "fact", "SELECT business_date AS date, property_id, business_vertical, revenue_usd FROM gold.mart_daily_revenue"),
    ("Room Occupancy", "room_occupancy", "fact", """SELECT business_date AS date, property_id, room_type, rooms_available, rooms_sold, room_revenue_usd
                                                    FROM gold.fact_room_occupancy WHERE is_actual"""),
    ("Room Revenue", "room_revenue", "fact", "SELECT business_date AS date, property_id, room_type, channel, segment, room_nights, room_revenue_usd FROM gold.fact_room_revenue"),
    ("Restaurant Covers", "restaurant_covers", "fact", """SELECT business_date AS date, property_id, outlet_code, time_slot, checks, covers::BIGINT AS covers, net_usd, food_cost_usd
                                                          FROM gold.fact_restaurant_covers"""),
    ("Waterpark Daily", "waterpark_daily", "fact", """SELECT business_date AS date, property_id, park_code AS outlet_code, daily_capacity, visitors::BIGINT AS visitors, net_usd, avg_queue_minutes
                                                      FROM gold.fact_waterpark_daily"""),
    ("Retail Sales", "retail_sales", "fact", """SELECT s.business_date AS date, s.property_id, s.store_code AS outlet_code, coalesce(p.category, 'Other') AS category,
                                                       sum(s.net_usd) AS net_usd, sum(s.cost_usd) AS cost_usd, sum(s.discount_usd) AS discount_usd, sum(s.gross_usd) AS gross_usd, sum(s.qty)::BIGINT AS units
                                                FROM gold.fact_retail_sales s LEFT JOIN gold.dim_product p ON p.product_key = s.sku AND p.product_type = 'Retail' GROUP BY ALL"""),
    ("Spa Appointments", "spa_appointments", "fact", """SELECT business_date AS date, property_id, spa_code AS outlet_code, service_category, status, count(*)::BIGINT AS appointments,
                                                               sum(net_usd) AS net_usd, sum(duration_minutes)::BIGINT AS duration_minutes
                                                        FROM gold.fact_spa_appointments GROUP BY ALL"""),
    ("Marketing", "marketing", "fact", """SELECT m.activity_date AS date, m.property_id, m.campaign_id, c.campaign_name, m.channel, m.target_vertical, m.audience_segment,
                                                 m.impressions, m.clicks, m.conversions, m.spend_usd, m.attributed_revenue_usd, m.new_customers
                                          FROM gold.fact_marketing_campaigns m LEFT JOIN gold.dim_campaign c USING (campaign_id)"""),
    ("Finance", "finance", "fact", """SELECT posting_date AS date, property_id, business_vertical, cost_centre, account_type, sum(amount_usd) AS amount_usd
                                      FROM gold.fact_financial_transactions GROUP BY ALL"""),
    ("Budget", "budget", "fact", """SELECT d.date, b.property_id, b.business_vertical, b.cost_centre, b.account_type,
                                           sum(b.budget_usd / date_part('day', last_day(b.period_start))) AS budget_usd
                                    FROM gold.fact_budget b JOIN gold.dim_date d ON d.date >= b.period_start AND d.date < b.period_start + INTERVAL 1 MONTH GROUP BY ALL"""),
    ("Forecasts", "forecasts", "fact", """SELECT business_date AS date, property_id, metric, forecast_type, forecast_value, lower_80, upper_80, actual_value
                                          FROM gold.fact_forecasts WHERE metric IN ('finance_revenue', 'finance_gop', 'finance_opex', 'hotel_room_revenue')"""),
    ("Recommendations", "recommendations", "fact", """SELECT recommendation_id, recommendation_date AS date, business_vertical, property_id, scenario, title, recommended_action,
                                                             estimated_impact_usd, confidence, priority, priority_score, approval_status, owner_role
                                                      FROM gold.fact_ai_recommendations"""),
    ("Alerts", "alerts", "fact", "SELECT alert_id, CAST(detected_at AS DATE) AS date, severity, category, business_vertical, property_id, title, status FROM ai.alerts"),
    ("Models", "models", "fact", """SELECT model_name, version, business_vertical AS domain, model_type, algorithm, metric_name, metric_value, baseline_value, drift_status, data_quality_status
                                    FROM ai.model_registry WHERE deployment_status = 'Deployed'"""),
    ("Energy Daily", "energy_daily", "fact", """SELECT b.reading_date AS date, b.property_id, b.kwh, b.expected_kwh, b.peak_kw, b.water_m3,
                                                       b.kwh * r.tariff_usd_kwh AS cost_usd, b.kwh * r.carbon_kg_kwh / 1000 AS carbon_t
                                                FROM ai.energy_baseline b JOIN ai.energy_rates r USING (property_id)"""),
    ("Energy End Use", "energy_end_use", "fact", "SELECT reading_date AS date, property_id, end_use, kwh FROM gold.fact_energy_daily"),
    ("Energy Forecast", "energy_forecast", "fact", "SELECT reading_date AS date, property_id, kwh AS forecast_kwh, lower_80, upper_80, cost_usd FROM ai.energy_forecast"),
    ("Device Risk", "device_risk", "fact", """SELECT asset_id, property_id, location, device_type, age_years, failure_probability, risk_band, remaining_life_months, under_warranty,
                                                     service_contract, nullif(replace_reason, '') AS replace_reason, action, replacement_cost_usd, cost_avoided_usd FROM ai.device_risk"""),
    ("Contract Reminders", "contract_reminders", "fact", """SELECT contract_name, property_id, supplier_name, covers, end_date, days_left, notice_deadline, annual_value_usd, devices_covered,
                                                                   failure_exposure_usd, status, action FROM ai.contract_reminders"""),
    ("Energy Findings", "energy_findings", "fact", "SELECT priority, category, property_id, title, detail, annual_value_usd, due_date FROM ai.energy_findings"),
]
KEYS = {"date": "Date", "property_id": "Property", "business_vertical": "Business Vertical", "outlet_code": "Outlet", "room_type": "Room Type", "channel": "Channel",
        "cost_centre": "Cost Centre"}
SORT_BY = {("Date", "month_name"): "month", ("Business Vertical", "business_vertical"): "sort_order"}
FORMATS = {"date": "Short Date", "end_date": "Short Date", "notice_deadline": "Short Date", "due_date": "Short Date"}
# Relationships that exist in the data but are left out of the Power BI model, because each fact already
# filters by property directly and a second path would be ambiguous. They are drawn in the schema diagram.
LOGICAL_ONLY = [("Outlet", "property_id", "Property", "property_id")]

# --------------------------------------------------------------------------------------------- measures
USD, USD2, PCT, NUM, DEC = "\\$#,0", "\\$#,0.00", "0.0%;-0.0%;0.0%", "#,0", "#,0.0"


def c(table: str, col: str) -> str:
    return f"'{table}'[{friendly(col)}]"


def s(table: str, col: str) -> str:
    return f"SUM({c(table, col)})"


E, RV, RO, RR, RC, WP, RS, SP, MK, FI, BU, FC, RE, AL, ED, EU, EF, DR, CR, FN = (
    "Executive Daily", "Revenue by Vertical", "Room Occupancy", "Room Revenue", "Restaurant Covers", "Waterpark Daily", "Retail Sales", "Spa Appointments", "Marketing", "Finance",
    "Budget", "Forecasts", "Recommendations", "Alerts", "Energy Daily", "Energy End Use", "Energy Forecast", "Device Risk", "Contract Reminders", "Energy Findings")
ACT = "'Date'[Is Actual] = TRUE()"
MEASURES: list[tuple[str, str, str, str]] = [
    # table, name, DAX, format
    (E, "Total Revenue", s(E, "total_revenue_usd"), USD), (E, "Room Revenue", s(E, "room_revenue_usd"), USD), (E, "Rooms Sold", s(E, "rooms_sold"), NUM),
    (E, "Rooms Available", s(E, "rooms_available"), NUM), (E, "Occupancy", "DIVIDE([Rooms Sold], [Rooms Available])", PCT), (E, "ADR", "DIVIDE([Room Revenue], [Rooms Sold])", USD),
    (E, "RevPAR", "DIVIDE([Room Revenue], [Rooms Available])", USD), (E, "GOP", s(E, "gop_usd"), USD), (E, "Operating Expenditure", s(E, "opex_usd"), USD),
    (E, "GOP Margin", f"DIVIDE([GOP], {s(E, 'gl_revenue_usd')})", PCT),
    (RV, "Vertical Revenue", s(RV, "revenue_usd"), USD),
    (RO, "Room Type Occupancy", f"DIVIDE({s(RO, 'rooms_sold')}, {s(RO, 'rooms_available')})", PCT), (RO, "Room Type Revenue", s(RO, "room_revenue_usd"), USD),
    (RO, "Room Type ADR", f"DIVIDE({s(RO, 'room_revenue_usd')}, {s(RO, 'rooms_sold')})", USD),
    (RR, "Room Nights", s(RR, "room_nights"), NUM), (RR, "Booked Room Revenue", s(RR, "room_revenue_usd"), USD), (RR, "Booked ADR", "DIVIDE([Booked Room Revenue], [Room Nights])", USD),
    (RC, "Covers", s(RC, "covers"), NUM), (RC, "Restaurant Sales", s(RC, "net_usd"), USD), (RC, "Checks", s(RC, "checks"), NUM),
    (RC, "Spend per Guest", "DIVIDE([Restaurant Sales], [Covers])", USD2), (RC, "Food Cost %", f"DIVIDE({s(RC, 'food_cost_usd')}, [Restaurant Sales])", PCT),
    (WP, "Visitors", s(WP, "visitors"), NUM), (WP, "Ticket Revenue", s(WP, "net_usd"), USD), (WP, "Revenue per Visitor", "DIVIDE([Ticket Revenue], [Visitors])", USD2),
    (WP, "Capacity Utilisation", f"DIVIDE([Visitors], {s(WP, 'daily_capacity')})", PCT), (WP, "Average Queue (min)", f"AVERAGE({c(WP, 'avg_queue_minutes')})", DEC),
    (RS, "Retail Sales Value", s(RS, "net_usd"), USD), (RS, "Retail Margin", f"DIVIDE({s(RS, 'net_usd')} - {s(RS, 'cost_usd')}, {s(RS, 'net_usd')})", PCT),
    (RS, "Units Sold", s(RS, "units"), NUM), (RS, "Discount Rate", f"DIVIDE({s(RS, 'discount_usd')}, {s(RS, 'gross_usd')})", PCT),
    (SP, "Completed Appointments", f"CALCULATE({s(SP, 'appointments')}, {c(SP, 'status')} = \"COMPLETED\")", NUM),
    (SP, "Spa Revenue", f"CALCULATE({s(SP, 'net_usd')}, {c(SP, 'status')} = \"COMPLETED\")", USD),
    (SP, "Spa Cancellation Rate", f"DIVIDE(CALCULATE({s(SP, 'appointments')}, {c(SP, 'status')} = \"CANCELLED\"), {s(SP, 'appointments')})", PCT),
    (MK, "Campaign Spend", s(MK, "spend_usd"), USD), (MK, "Attributed Revenue", s(MK, "attributed_revenue_usd"), USD),
    (MK, "Campaign ROI", "DIVIDE([Attributed Revenue] - [Campaign Spend], [Campaign Spend])", PCT), (MK, "Conversions", s(MK, "conversions"), NUM), (MK, "Clicks", s(MK, "clicks"), NUM),
    (MK, "Conversion Rate", "DIVIDE([Conversions], [Clicks])", PCT), (MK, "Acquisition Cost", f"DIVIDE([Campaign Spend], {s(MK, 'new_customers')})", USD),
    (FI, "Ledger Revenue", f"CALCULATE({s(FI, 'amount_usd')}, {c(FI, 'account_type')} = \"REVENUE\")", USD),
    (FI, "Ledger Expenses", f"CALCULATE({s(FI, 'amount_usd')}, {c(FI, 'account_type')} = \"EXPENSE\")", USD),
    (FI, "Ledger GOP", "[Ledger Revenue] - [Ledger Expenses]", USD), (FI, "Ledger GOP Margin", "DIVIDE([Ledger GOP], [Ledger Revenue])", PCT),
    (BU, "Revenue Budget", f"CALCULATE({s(BU, 'budget_usd')}, {c(BU, 'account_type')} = \"REVENUE\", {ACT})", USD),
    (BU, "Expense Budget", f"CALCULATE({s(BU, 'budget_usd')}, {c(BU, 'account_type')} = \"EXPENSE\", {ACT})", USD),
    (BU, "Revenue vs Budget", "DIVIDE([Ledger Revenue] - [Revenue Budget], [Revenue Budget])", PCT),
    (BU, "Expenses vs Budget", "DIVIDE([Ledger Expenses] - [Expense Budget], [Expense Budget])", PCT),
    (BU, "Expense Variance", "[Ledger Expenses] - [Expense Budget]", USD),
    (FC, "Revenue Forecast", f"CALCULATE({s(FC, 'forecast_value')}, {c(FC, 'metric')} = \"finance_revenue\", {c(FC, 'forecast_type')} = \"forecast\")", USD),
    (FC, "Revenue Forecast Low", f"CALCULATE({s(FC, 'lower_80')}, {c(FC, 'metric')} = \"finance_revenue\", {c(FC, 'forecast_type')} = \"forecast\")", USD),
    (FC, "Revenue Forecast High", f"CALCULATE({s(FC, 'upper_80')}, {c(FC, 'metric')} = \"finance_revenue\", {c(FC, 'forecast_type')} = \"forecast\")", USD),
    (FC, "Revenue Actual (last 90 days)", f"CALCULATE({s(FC, 'actual_value')}, {c(FC, 'metric')} = \"finance_revenue\", {c(FC, 'forecast_type')} = \"backtest\")", USD),
    (FC, "Revenue Backtest Forecast", f"CALCULATE({s(FC, 'forecast_value')}, {c(FC, 'metric')} = \"finance_revenue\", {c(FC, 'forecast_type')} = \"backtest\")", USD),
    (FC, "GOP Forecast", f"CALCULATE({s(FC, 'forecast_value')}, {c(FC, 'metric')} = \"finance_gop\", {c(FC, 'forecast_type')} = \"forecast\")", USD),
    (RE, "Recommendations Count", f"COUNTROWS('{RE}')", NUM), (RE, "Estimated Impact", s(RE, "estimated_impact_usd"), USD),
    (RE, "Average Confidence", f"AVERAGE({c(RE, 'confidence')})", PCT),
    (AL, "Open Alerts", f"CALCULATE(COUNTROWS('{AL}'), {c(AL, 'status')} <> \"Resolved\")", NUM),
    (AL, "Critical Alerts", f"CALCULATE(COUNTROWS('{AL}'), {c(AL, 'status')} <> \"Resolved\", {c(AL, 'severity')} = \"Critical\")", NUM),
    (ED, "Energy (MWh)", f"{s(ED, 'kwh')} / 1000", DEC), (ED, "Energy (kWh)", s(ED, "kwh"), NUM), (ED, "Expected Energy (kWh)", s(ED, "expected_kwh"), NUM),
    (ED, "Energy vs Expected", "DIVIDE([Energy (kWh)] - [Expected Energy (kWh)], [Expected Energy (kWh)])", "+0.0%;-0.0%;0.0%"), (ED, "Energy Cost", s(ED, "cost_usd"), USD),
    (ED, "Carbon (t CO2e)", s(ED, "carbon_t"), NUM), (ED, "Water (m3)", s(ED, "water_m3"), NUM),
    (EU, "End-use Energy (MWh)", f"{s(EU, 'kwh')} / 1000", DEC),
    (EF, "Forecast Energy (kWh)", s(EF, "forecast_kwh"), NUM), (EF, "Forecast Energy Cost", s(EF, "cost_usd"), USD),
    (DR, "Devices", f"COUNTROWS('{DR}')", NUM), (DR, "High-risk Devices", f"CALCULATE(COUNTROWS('{DR}'), {c(DR, 'risk_band')} = \"High\")", NUM),
    (DR, "Expected Failures (30 days)", s(DR, "failure_probability"), DEC),
    (DR, "Devices Due for Replacement", f"CALCULATE(COUNTROWS('{DR}'), NOT ISBLANK({c(DR, 'replace_reason')}))", NUM),
    (DR, "Replacement Cost", f"CALCULATE({s(DR, 'replacement_cost_usd')}, NOT ISBLANK({c(DR, 'replace_reason')}))", USD),
    (CR, "Contracts Needing Action", f"COUNTROWS('{CR}')", NUM), (CR, "Contract Annual Value", s(CR, "annual_value_usd"), USD), (CR, "Repair Exposure", s(CR, "failure_exposure_usd"), USD),
    (FN, "Findings", f"COUNTROWS('{FN}')", NUM), (FN, "Findings Annual Value", s(FN, "annual_value_usd"), USD),
]
M_TABLE = {name: table for table, name, _, _ in MEASURES}

# --------------------------------------------------------------------------------------------- pages
D, P, BV, O = "Date", "Property", "Business Vertical", "Outlet"
BAR, COL, STACK, LINE, TABLE, DONUT = "clusteredBarChart", "clusteredColumnChart", "columnChart", "lineChart", "tableEx", "donutChart"
# Each page: (title, [card measures], [(visual type, title, category field, [value measures], series field)])
# A field is ("Table", "source_column"). Four visuals per page: top-left (wide), top-right, bottom-left (wide), bottom-right.
PAGES = [
    ("Executive", ["Total Revenue", "Occupancy", "ADR", "RevPAR", "GOP", "GOP Margin"], [
        (LINE, "Total revenue by day", (D, "date"), ["Total Revenue"], None),
        (BAR, "Revenue by business vertical", (BV, "business_vertical"), ["Vertical Revenue"], None),
        (TABLE, "Performance by property", (P, "property"), ["Total Revenue", "Occupancy", "ADR", "RevPAR", "GOP", "GOP Margin"], None),
        (STACK, "Revenue mix by property", (P, "property"), ["Vertical Revenue"], (BV, "business_vertical"))]),
    ("Hotels", ["Occupancy", "ADR", "RevPAR", "Room Revenue", "Rooms Sold"], [
        (LINE, "Occupancy by day", (D, "date"), ["Occupancy"], None),
        (BAR, "Room revenue by booking channel", ("Channel", "channel"), ["Booked Room Revenue"], None),
        (TABLE, "Performance by room category", ("Room Type", "room_type_name"), ["Room Type Occupancy", "Room Type ADR", "Room Type Revenue"], None),
        (LINE, "ADR and RevPAR by month", (D, "year_month"), ["ADR", "RevPAR"], None)]),
    ("Restaurants", ["Covers", "Restaurant Sales", "Spend per Guest", "Food Cost %", "Checks"], [
        (LINE, "Covers by day", (D, "date"), ["Covers"], None),
        (COL, "Sales by meal period", (RC, "time_slot"), ["Restaurant Sales"], None),
        (TABLE, "Performance by restaurant", (O, "outlet_name"), ["Covers", "Restaurant Sales", "Spend per Guest", "Food Cost %"], None),
        (BAR, "Sales by restaurant", (O, "outlet_name"), ["Restaurant Sales"], None)]),
    ("Waterpark", ["Visitors", "Ticket Revenue", "Revenue per Visitor", "Capacity Utilisation", "Average Queue (min)"], [
        (LINE, "Visitors by day", (D, "date"), ["Visitors"], None),
        (COL, "Capacity utilisation by month", (D, "year_month"), ["Capacity Utilisation"], None),
        (TABLE, "Performance by park", (O, "outlet_name"), ["Visitors", "Ticket Revenue", "Revenue per Visitor", "Capacity Utilisation", "Average Queue (min)"], None),
        (LINE, "Average queue by month (minutes)", (D, "year_month"), ["Average Queue (min)"], None)]),
    ("Retail and Spa", ["Retail Sales Value", "Retail Margin", "Units Sold", "Spa Revenue", "Completed Appointments", "Spa Cancellation Rate"], [
        (LINE, "Retail sales by day", (D, "date"), ["Retail Sales Value"], None),
        (BAR, "Retail sales by category", (RS, "category"), ["Retail Sales Value"], None),
        (LINE, "Completed spa appointments by day", (D, "date"), ["Completed Appointments"], None),
        (BAR, "Spa revenue by service category", (SP, "service_category"), ["Spa Revenue"], None)]),
    ("Marketing", ["Campaign Spend", "Attributed Revenue", "Campaign ROI", "Conversions", "Conversion Rate", "Acquisition Cost"], [
        (LINE, "Campaign spend and attributed revenue by month", (D, "year_month"), ["Campaign Spend", "Attributed Revenue"], None),
        (BAR, "Return on investment by channel", ("Channel", "channel"), ["Campaign ROI"], None),
        (TABLE, "Performance by channel", ("Channel", "channel"), ["Campaign Spend", "Clicks", "Conversions", "Conversion Rate", "Acquisition Cost", "Attributed Revenue", "Campaign ROI"], None),
        (BAR, "Spend by audience segment", (MK, "audience_segment"), ["Campaign Spend"], None)]),
    ("Finance", ["Ledger Revenue", "Ledger Expenses", "Ledger GOP", "Ledger GOP Margin", "Revenue vs Budget", "Expenses vs Budget"], [
        (LINE, "Revenue against budget by month", (D, "year_month"), ["Ledger Revenue", "Revenue Budget"], None),
        (LINE, "Expenses against budget by month", (D, "year_month"), ["Ledger Expenses", "Expense Budget"], None),
        (TABLE, "Cost centres against budget", ("Cost Centre", "cost_centre"), ["Ledger Expenses", "Expense Budget", "Expense Variance", "Expenses vs Budget"], None),
        (TABLE, "Profitability by property", (P, "property"), ["Ledger Revenue", "Ledger Expenses", "Ledger GOP", "Ledger GOP Margin"], None)]),
    ("AI Control Tower", ["Revenue Forecast", "GOP Forecast", "Recommendations Count", "Estimated Impact", "Open Alerts", "Critical Alerts"], [
        (LINE, "Revenue: last 90 days and forecast for the next 90", (D, "date"), ["Revenue Actual (last 90 days)", "Revenue Forecast", "Revenue Forecast Low", "Revenue Forecast High"], None),
        (BAR, "Estimated impact of recommendations by domain", (BV, "business_vertical"), ["Estimated Impact"], None),
        (TABLE, "Recommendations", (RE, "title"), ["Estimated Impact", "Average Confidence"], [(RE, "priority"), (P, "property"), (RE, "approval_status")]),
        (TABLE, "Alerts", (AL, "title"), ["Open Alerts"], [(AL, "severity"), (AL, "category"), (P, "property")])]),
    ("Energy and Assets", ["Energy (MWh)", "Energy Cost", "Energy vs Expected", "Carbon (t CO2e)", "High-risk Devices", "Expected Failures (30 days)", "Contracts Needing Action"], [
        (LINE, "Energy: actual, expected and forecast (kWh)", (D, "date"), ["Energy (kWh)", "Expected Energy (kWh)", "Forecast Energy (kWh)"], None),
        (STACK, "Energy by end use and property (MWh)", (P, "property"), ["End-use Energy (MWh)"], (EU, "end_use")),
        (TABLE, "Contract reminders", (CR, "contract_name"), ["Contract Annual Value", "Repair Exposure"], [(P, "property"), (CR, "supplier_name"), (CR, "end_date"), (CR, "status")]),
        (BAR, "Expected failures in the next 30 days by device type", (DR, "device_type"), ["Expected Failures (30 days)"], None)]),
]


# --------------------------------------------------------------------------------------------- helpers
def gid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "aurelian/" + "/".join(parts)))


def vid(*parts: str) -> str:
    return hashlib.sha1("/".join(parts).encode()).hexdigest()[:20]


def q(name: str) -> str:
    """Quote a TMDL object name when it needs it."""
    return name if name.replace("_", "").isalnum() else "'" + name.replace("'", "''") + "'"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def dump(path: Path, obj) -> None:
    write(path, json.dumps(obj, indent=2) + "\n")


def lit(v) -> dict:
    if isinstance(v, bool):
        return {"expr": {"Literal": {"Value": "true" if v else "false"}}}
    if isinstance(v, (int, float)):
        return {"expr": {"Literal": {"Value": f"{v}D"}}}
    return {"expr": {"Literal": {"Value": "'" + str(v).replace("'", "''") + "'"}}}


# --------------------------------------------------------------------------------------------- data + model
TYPES = {"VARCHAR": "string", "DOUBLE": "double", "FLOAT": "double", "BIGINT": "int64", "INTEGER": "int64", "SMALLINT": "int64", "TINYINT": "int64", "DATE": "dateTime",
         "TIMESTAMP": "dateTime", "BOOLEAN": "boolean"}


def export() -> dict[str, list[tuple[str, str]]]:
    """Write one Parquet file per model table; return {table: [(source column, model data type)]}."""
    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True)
    con = duckdb.connect(str(ROOT / "data" / "lakehouse.duckdb"), read_only=True)
    cols = {}
    for table, file, _, sql in TABLES:
        desc = con.execute(f"DESCRIBE {sql}").fetchall()
        cols[table] = [(r[0], TYPES[r[1].split("(")[0]]) for r in desc]
        con.execute(f"COPY ({sql}) TO '{(DATA / (file + '.parquet')).as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        rows = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
        print(f"  {table:<22}{rows:>9,} rows  {len(desc):>2} columns")
    con.close()
    return cols


def relationships(cols) -> list[tuple[str, str, str, str]]:
    rel = []
    for table, _, kind, _ in TABLES:
        if kind != "fact":
            continue
        for col, _t in cols[table]:
            if col in KEYS:
                rel.append((table, col, KEYS[col], col))
    return rel


def model(cols) -> None:
    if SM.exists():
        shutil.rmtree(SM)
    dump(SM / ".platform", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                            "metadata": {"type": "SemanticModel", "displayName": NAME}, "config": {"version": "2.0", "logicalId": gid("model")}})
    dump(SM / "definition.pbism", {"version": "4.2", "settings": {}})
    d = SM / "definition"
    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1567\n")
    refs = "".join(f"ref table {q(t)}\n" for t, *_ in TABLES)
    write(d / "model.tmdl", "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n\tdataAccessOptions\n\t\tlegacyRedirects\n"
                            "\t\treturnErrorValuesAsNull\n\n\tannotation __PBI_TimeIntelligenceEnabled = 0\n\n" + refs + "\nref expression DataFolder\n")
    folder = str(DATA).replace('"', '""')
    write(d / "expressions.tmdl", f'expression DataFolder = "{folder}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
                                  f"\tlineageTag: {gid('expr')}\n\n\tannotation PBI_ResultType = Text\n")
    fact_keys = set(KEYS)
    for table, file, kind, _ in TABLES:
        out = [f"table {q(table)}", f"\tlineageTag: {gid('t', table)}"]
        if table == "Date":
            out.append("\tdataCategory: Time")
        out.append("")
        for t, name, dax, fmt in MEASURES:
            if t == table:
                out += [f"\tmeasure {q(name)} = {dax}", f"\t\tformatString: {fmt}", f"\t\tlineageTag: {gid('m', name)}", ""]
        for col, typ in cols[table]:
            out.append(f"\tcolumn {q(friendly(col))}")
            out.append(f"\t\tdataType: {typ}")
            if kind == "dim" and col == cols[table][0][0]:
                out.append("\t\tisKey")
            if kind == "fact" and (col in fact_keys or col.endswith("_id")):
                out.append("\t\tisHidden")
            if typ == "dateTime":
                out.append(f"\t\tformatString: {FORMATS.get(col, 'Short Date')}")
            elif typ == "double":
                out.append("\t\tformatString: #,0.00")
            elif typ == "int64":
                out.append("\t\tformatString: 0")
            out.append(f"\t\tlineageTag: {gid('c', table, col)}")
            out.append("\t\tsummarizeBy: none")
            if (table, col) in SORT_BY:
                out.append(f"\t\tsortByColumn: {q(friendly(SORT_BY[(table, col)]))}")
            out.append(f"\t\tsourceColumn: {col}")
            out.append("")
        out += [f"\tpartition {q(table)} = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet",
                f'\t\t\t\t    Source = Parquet.Document(File.Contents(DataFolder & "\\{file}.parquet"))', "\t\t\t\tin", "\t\t\t\t    Source", ""]
        write(d / "tables" / f"{table}.tmdl", "\n".join(out))
    rel = [f"relationship {gid('r', a, ac, b)}\n\tfromColumn: {q(a)}.{q(friendly(ac))}\n\ttoColumn: {q(b)}.{q(friendly(bc))}\n" for a, ac, b, bc in relationships(cols)]
    write(d / "relationships.tmdl", "\n".join(rel))


# --------------------------------------------------------------------------------------------- report
def col_field(table: str, col: str) -> dict:
    name = friendly(col)
    return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}, "queryRef": f"{table}.{name}", "nativeQueryRef": name}


def measure_field(name: str) -> dict:
    t = M_TABLE[name]
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": t}}, "Property": name}}, "queryRef": f"{t}.{name}", "nativeQueryRef": name}


def container(page: str, key: str, x, y, w, h, z, visual: dict) -> tuple[str, dict]:
    name = vid(page, key)
    return name, {"$schema": f"{SCHEMA_BASE}definition/visualContainer/{V_VISUAL}/schema.json", "name": name,
                  "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z}, "visual": visual}


def titled(text: str) -> dict:
    return {"title": [{"properties": {"show": lit(True), "text": lit(text), "fontSize": lit(11), "bold": lit(True), "fontColor": {"solid": {"color": lit(NAVY)}}}}]}


def textbox(text: str, size: int, color: str, bold: bool, background: str | None) -> dict:
    v = {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [{"textRuns": [
        {"value": text, "textStyle": {"fontSize": f"{size}pt", "color": color, **({"fontWeight": "bold"} if bold else {})}}]}]}}]},
        "drillFilterOtherVisuals": True}
    vco = {"title": [{"properties": {"show": lit(False)}}]}
    if background:
        vco["background"] = [{"properties": {"show": lit(True), "color": {"solid": {"color": lit(background)}}, "transparency": lit(0)}}]
    v["visualContainerObjects"] = vco
    return v


def slicer(table: str, col: str, mode: str, title: str, group: str) -> dict:
    return {"visualType": "slicer", "query": {"queryState": {"Values": {"projections": [col_field(table, col)]}}},
            "objects": {"data": [{"properties": {"mode": lit(mode)}}], "header": [{"properties": {"show": lit(False)}}]},
            "visualContainerObjects": titled(title), "syncGroup": {"groupName": group, "fieldChanges": True, "filterChanges": True}, "drillFilterOtherVisuals": True}


def card(measure: str) -> dict:
    return {"visualType": "card", "query": {"queryState": {"Values": {"projections": [measure_field(measure)]}}},
            "objects": {"labels": [{"properties": {"fontSize": lit(20), "color": {"solid": {"color": lit(NAVY)}}}}],
                        "categoryLabels": [{"properties": {"fontSize": lit(10)}}]},
            "visualContainerObjects": {"title": [{"properties": {"show": lit(False)}}],
                                       "border": [{"properties": {"show": lit(True), "color": {"solid": {"color": lit("#D9DEE3")}}, "radius": lit(6)}}]},
            "drillFilterOtherVisuals": True}


def chart(kind: str, title: str, category, values: list[str], extra) -> dict:
    if kind == TABLE:
        fields = [col_field(*category)] + [col_field(*f) for f in (extra or [])] + [measure_field(m) for m in values]
        state = {"Values": {"projections": fields}}
    else:
        state = {"Category": {"projections": [col_field(*category)]}, "Y": {"projections": [measure_field(m) for m in values]}}
        if extra:
            state["Series"] = {"projections": [col_field(*extra)]}
    query = {"queryState": state}
    if kind in (BAR, COL, TABLE) and not (kind == COL and category[0] == D):
        query["sortDefinition"] = {"sort": [{"field": measure_field(values[0])["field"], "direction": "Descending"}], "isDefaultSort": False}
    v = {"visualType": kind, "query": query, "visualContainerObjects": titled(title), "drillFilterOtherVisuals": True}
    if kind in (LINE, STACK) and (len(values) > 1 or extra):
        v["objects"] = {"legend": [{"properties": {"show": lit(True), "position": lit("Top")}}]}
    return v


def report() -> int:
    if RP.exists():
        shutil.rmtree(RP)
    dump(RP / ".platform", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                            "metadata": {"type": "Report", "displayName": NAME}, "config": {"version": "2.0", "logicalId": gid("report")}})
    dump(RP / "definition.pbir", {"$schema": f"{SCHEMA_BASE}definitionProperties/2.0.0/schema.json", "version": "4.0",
                                  "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    d = RP / "definition"
    dump(d / "version.json", {"$schema": f"{SCHEMA_BASE}definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    theme = "Aurelian.json"
    dump(RP / "StaticResources" / "RegisteredResources" / theme, {
        "name": "Aurelian", "dataColors": PALETTE, "background": "#FFFFFF", "foreground": "#1E1E1E", "tableAccent": NAVY,
        "textClasses": {"title": {"color": NAVY, "fontFace": "Segoe UI Semibold"}, "label": {"color": "#3A4652"}}})
    dump(d / "report.json", {
        "$schema": f"{SCHEMA_BASE}definition/report/{V_REPORT}/schema.json",
        "themeCollection": {"customTheme": {"name": theme, "reportVersionAtImport": {"visual": V_VISUAL, "page": V_PAGE, "report": V_REPORT}, "type": "RegisteredResources"}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources", "items": [{"name": theme, "path": theme, "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True}})
    order, count = [], 0
    for title, cards, visuals in PAGES:
        page = vid("page", title)
        order.append(page)
        dump(d / "pages" / page / "page.json", {"$schema": f"{SCHEMA_BASE}definition/page/{V_PAGE}/schema.json", "name": page, "displayName": title,
                                               "displayOption": "FitToPage", "height": 720, "width": 1280})
        items = [("header", 0, 0, 1280, 54, textbox(f"Aurelian Collection  |  {title}", 18, "#FFFFFF", True, NAVY)),
                 ("slicer_property", 16, 62, 300, 62, slicer(P, "property", "Dropdown", "Property", "Property")),
                 ("slicer_date", 328, 62, 420, 62, slicer(D, "date", "Between", "Period", "Period")),
                 ("note", 764, 66, 500, 54, textbox(f"Synthetic data for a fictional six-property chain. Last closed business date: {AS_OF:%d %B %Y}. Amounts in US dollars.",
                                                    9, "#5F6B76", False, None))]
        gap, n = 10, len(cards)
        cw = (1248 - gap * (n - 1)) / n
        for i, m in enumerate(cards):
            items.append((f"card_{i}", round(16 + i * (cw + gap), 1), 134, round(cw, 1), 92, card(m)))
        boxes = [(16, 236, 760, 232), (788, 236, 476, 232), (16, 478, 760, 232), (788, 478, 476, 232)]
        for i, (kind, vtitle, category, values, extra) in enumerate(visuals):
            items.append((f"v{i}", *boxes[i], chart(kind, vtitle, category, values, extra)))
        for z, (key, x, y, w, h, visual) in enumerate(items):
            name, body = container(title, key, x, y, w, h, z * 1000, visual)
            dump(d / "pages" / page / "visuals" / name / "visual.json", body)
            count += 1
    dump(d / "pages" / "pages.json", {"$schema": f"{SCHEMA_BASE}definition/pagesMetadata/{V_PAGES}/schema.json", "pageOrder": order, "activePageName": order[0]})
    return count


def check_refs(cols) -> None:
    """Every field a visual or measure uses must exist in the model."""
    have = {(t, friendly(col)) for t in cols for col, _ in cols[t]}
    for title, cards, visuals in PAGES:
        for m in cards:
            assert m in M_TABLE, (title, m)
        for kind, vtitle, category, values, extra in visuals:
            fields = [category] + (extra if isinstance(extra, list) else [extra] if extra else [])
            for t, col in fields:
                assert (t, friendly(col)) in have, (title, vtitle, t, col)
            for m in values:
                assert m in M_TABLE, (title, vtitle, m)
    import re
    names = [name for _, name, _, _ in MEASURES]
    assert len(names) == len(set(names)), "duplicate measure name"
    for t, name, dax, _ in MEASURES:
        assert (t, name) not in have, f"measure and column share a name: {t}.{name}"
        for tt, cc in re.findall(r"'([^']+)'\[([^\]]+)\]", dax):
            assert (tt, cc) in have, (name, tt, cc)
        for mm in re.findall(r"(?<!')\[([^\]]+)\]", dax):
            assert mm in M_TABLE, (name, mm)


def validate() -> int:
    """Validate every report JSON file against Microsoft's published schema for it."""
    import urllib.request

    from jsonschema import Draft7Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    SCHEMA_CACHE.mkdir(parents=True, exist_ok=True)

    def retrieve(uri: str):
        f = SCHEMA_CACHE / (hashlib.sha1(uri.encode()).hexdigest()[:16] + ".json")
        if not f.exists():
            f.write_bytes(urllib.request.urlopen(urllib.request.Request(uri, headers={"User-Agent": "schema-validator"}), timeout=60).read())
        return Resource.from_contents(json.loads(f.read_text(encoding="utf-8")), default_specification=DRAFT7)

    registry, problems, n = Registry(retrieve=retrieve), 0, 0
    for f in sorted(RP.rglob("*.json")) + [RP / "definition.pbir"]:
        body = json.loads(f.read_text(encoding="utf-8"))
        uri = body.get("$schema")
        if not uri or "fabric/item/report" not in uri:
            continue
        n += 1
        schema = retrieve(uri).contents
        for err in list(Draft7Validator(schema, registry=registry).iter_errors(body))[:3]:
            problems += 1
            print(f"  SCHEMA {f.relative_to(RP)}: {'/'.join(map(str, err.absolute_path))}: {err.message[:200]}")
    print(f"schema check: {n} files, {problems} problem(s)")
    return problems


def main() -> None:
    OUT.mkdir(exist_ok=True)
    print("Exporting data")
    cols = export()
    check_refs(cols)
    model(cols)
    n = report()
    dump(OUT / f"{NAME}.pbip", {"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}})
    rel = relationships(cols)
    dump(OUT / "model.json", {
        "tables": [{"name": t, "kind": kind, "source": sql.split("FROM", 1)[1].split()[0] if "UNION" not in sql else "gold.fact_financial_transactions, gold.fact_budget",
                    "columns": [{"name": friendly(col), "source": col, "type": typ} for col, typ in cols[t]]} for t, _, kind, sql in TABLES],
        "relationships": [{"from": a, "from_column": friendly(ac), "to": b, "to_column": friendly(bc), "in_model": True} for a, ac, b, bc in rel]
                         + [{"from": a, "from_column": friendly(ac), "to": b, "to_column": friendly(bc), "in_model": False} for a, ac, b, bc in LOGICAL_ONLY],
        "measures": [{"table": t, "name": name, "expression": dax, "format": fmt} for t, name, dax, fmt in MEASURES]})
    size = sum(f.stat().st_size for f in DATA.glob("*.parquet")) / 1e6
    print(f"Wrote {OUT}: {len(TABLES)} tables, {len(rel)} relationships, {len(MEASURES)} measures, {len(PAGES)} pages, {n} visuals, {size:.1f} MB of data")
    if "--validate" in sys.argv:
        sys.exit(1 if validate() else 0)


if __name__ == "__main__":
    main()
