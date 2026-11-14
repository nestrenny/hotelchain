"""Gold layer: conformed dimensions, fact tables at their natural grain, and business marts
(SRS 4.3, 8.2). Natural business keys are used as dimension keys to keep the prototype readable;
the Fabric build would add surrogate keys in the Warehouse."""
from __future__ import annotations

from hip.lake import build
from hip.settings import AS_OF, HISTORY_START, load

A = f"DATE '{AS_OF}'"
H = f"DATE '{HISTORY_START}'"

TABLES: list[tuple[str, str]] = [
    # ------------------------------------------------------------------ dimensions
    ("gold.dim_property", "SELECT * FROM silver.property"),
    ("gold.dim_business_vertical", """
        SELECT * FROM (VALUES ('Hotels', 1, true), ('Restaurants', 2, true), ('Waterpark', 3, true), ('Retail', 4, true), ('Spa & Leisure', 5, true),
                              ('Marketing', 6, false), ('Finance', 7, false)) t(business_vertical, sort_order, is_revenue_generating)"""),
    ("gold.dim_date", f"""
        SELECT CAST(d AS DATE) AS date, year(d) AS year, quarter(d) AS quarter, month(d) AS month, strftime(d, '%b') AS month_name, strftime(d, '%Y-%m') AS year_month,
               weekofyear(d) AS iso_week, isodow(d) AS day_of_week, strftime(d, '%a') AS day_name, isodow(d) >= 6 AS is_weekend,
               year(d) AS fiscal_year, strftime(d, '%Y-%m') AS fiscal_period, CAST(d AS DATE) <= {A} AS is_closed
        FROM generate_series({H}, {A} + INTERVAL 150 DAY, INTERVAL 1 DAY) t(d)"""),
    ("gold.dim_time", """
        SELECT h AS hour_of_day, lpad(CAST(h AS VARCHAR), 2, '0') || ':00' AS hour_label,
               CASE WHEN h < 11 THEN 'Breakfast' WHEN h < 15 THEN 'Lunch' WHEN h < 18 THEN 'Afternoon' ELSE 'Dinner' END AS time_slot FROM range(24) t(h)"""),
    ("gold.dim_guest", """
        SELECT guest_key, display_name, email_hash, nationality, loyalty_tier, is_member, marketing_consent, member_since, source_systems, source_records FROM silver.guest"""),
    ("gold.dim_customer", "SELECT guest_key, customer_id, loyalty_id, loyalty_tier, marketing_consent, member_since FROM silver.guest WHERE is_member"),
    ("gold.dim_customer_history", "SELECT guest_key, customer_id, loyalty_tier, marketing_consent, valid_from, valid_to, is_current FROM silver.customer_history"),
    ("gold.dim_room", "SELECT room_type, room_type_name FROM silver.room_type"),
    ("gold.dim_outlet", "SELECT * FROM silver.outlet"),
    ("gold.dim_attraction", "SELECT * FROM silver.attraction"),
    ("gold.dim_product", """
        SELECT sku AS product_key, 'Retail' AS product_type, product_name, category, NULL AS outlet_code FROM silver.product
        UNION ALL SELECT menu_item_code, 'Menu Item', item_name, category, outlet_code FROM silver.menu_item
        UNION ALL SELECT service_code, 'Spa Service', service_name, service_category, NULL FROM silver.spa_service"""),
    ("gold.dim_supplier", "SELECT * FROM silver.supplier"),
    ("gold.dim_campaign", "SELECT * FROM silver.campaign"),
    ("gold.dim_channel", """
        SELECT DISTINCT channel, 'Hotel booking' AS channel_type, channel IN ('Direct Web', 'Phone') AS is_direct FROM silver.reservation WHERE channel IS NOT NULL
        UNION ALL SELECT DISTINCT channel, 'Marketing', false FROM silver.campaign"""),
    ("gold.dim_employee", "SELECT * FROM silver.employee"),
    ("gold.dim_currency", f"SELECT currency, arg_max(usd_per_unit, rate_date) AS latest_usd_per_unit, avg(usd_per_unit) AS avg_usd_per_unit FROM silver.fx_rate GROUP BY 1"),
    ("gold.dim_event_calendar", "SELECT * FROM silver.event_calendar"),
    # ------------------------------------------------------------------ hotel facts
    ("gold.fact_reservations", "SELECT * FROM silver.reservation"),
    ("gold.fact_room_occupancy", f"""
        WITH s AS (SELECT property_id, room_type, stay_date, count(*) FILTER (WHERE is_actual) AS rooms_sold, sum(revenue_local) FILTER (WHERE is_actual) AS room_revenue_local,
                          sum(revenue_usd) FILTER (WHERE is_actual) AS room_revenue_usd, count(*) FILTER (WHERE NOT is_actual) AS rooms_on_books,
                          sum(revenue_usd) FILTER (WHERE NOT is_actual) AS revenue_on_books_usd
                   FROM silver.room_night GROUP BY ALL)
        SELECT i.property_id, i.room_type, i.business_date, i.rooms_total, i.rooms_out_of_order, i.rooms_available, coalesce(s.rooms_sold, 0) AS rooms_sold,
               coalesce(s.room_revenue_local, 0) AS room_revenue_local, coalesce(s.room_revenue_usd, 0) AS room_revenue_usd,
               coalesce(s.rooms_on_books, 0) AS rooms_on_books, coalesce(s.revenue_on_books_usd, 0) AS revenue_on_books_usd, i.business_date <= {A} AS is_actual
        FROM silver.room_inventory i LEFT JOIN s ON s.property_id = i.property_id AND s.room_type = i.room_type AND s.stay_date = i.business_date"""),
    ("gold.fact_room_revenue", """
        SELECT property_id, stay_date AS business_date, room_type, channel, segment, count(*) AS room_nights, sum(revenue_local) AS room_revenue_local, sum(revenue_usd) AS room_revenue_usd
        FROM silver.room_night WHERE is_actual GROUP BY ALL"""),
    ("gold.fact_guest_feedback", "SELECT * FROM silver.guest_feedback"),
    # ------------------------------------------------------------------ restaurant facts
    ("gold.fact_restaurant_transactions", "SELECT * FROM silver.restaurant_line"),
    ("gold.fact_restaurant_covers", """
        WITH chk AS (SELECT check_id, outlet_code, property_id, business_date, time_slot, any_value(covers) AS covers, any_value(reservation_ref) IS NOT NULL AS reserved,
                            sum(net_usd) AS net_usd, sum(net_local) AS net_local, sum(food_cost_usd) AS food_cost_usd, sum(discount_usd) AS discount_usd
                     FROM silver.restaurant_line GROUP BY ALL)
        SELECT outlet_code, property_id, business_date, time_slot, count(*) AS checks, sum(covers) AS covers, coalesce(sum(covers) FILTER (WHERE reserved), 0) AS reserved_covers,
               coalesce(sum(covers) FILTER (WHERE NOT reserved), 0) AS walk_in_covers, sum(net_usd) AS net_usd, sum(net_local) AS net_local,
               sum(food_cost_usd) AS food_cost_usd, sum(discount_usd) AS discount_usd
        FROM chk GROUP BY ALL"""),
    ("gold.fact_table_reservations", "SELECT * FROM silver.table_reservation"),
    # ------------------------------------------------------------------ waterpark facts
    ("gold.fact_waterpark_attendance", """
        SELECT park_code, property_id, visit_date AS business_date, ticket_category, sales_channel, count(*) AS transactions, sum(visitors) AS visitors,
               sum(gross_local) AS gross_local, sum(net_local) AS net_local, sum(net_usd) AS net_usd, sum(discount_usd) AS discount_usd, avg(dwell_minutes) AS avg_dwell_minutes
        FROM silver.waterpark_ticket GROUP BY ALL"""),
    ("gold.fact_attraction_utilisation", "SELECT *, riders / nullif(capacity, 0) AS utilisation FROM silver.attraction_hourly"),
    ("gold.fact_waterpark_daily", """
        WITH a AS (SELECT park_code, business_date, sum(visitors) AS visitors, sum(net_usd) AS net_usd, sum(transactions) AS transactions FROM gold.fact_waterpark_attendance GROUP BY ALL),
             q AS (SELECT park_code, business_date, sum(avg_queue_minutes * riders) / nullif(sum(riders), 0) AS avg_queue_minutes, max(avg_queue_minutes) AS peak_queue_minutes,
                          sum(riders) AS rides, sum(capacity) AS ride_capacity FROM silver.attraction_hourly GROUP BY ALL)
        SELECT d.park_code, d.property_id, d.business_date, d.daily_capacity, coalesce(a.visitors, 0) AS visitors, coalesce(a.visitors, 0) / d.daily_capacity AS capacity_utilisation,
               coalesce(a.net_usd, 0) AS net_usd, coalesce(a.transactions, 0) AS transactions, d.max_temp_c, d.rain, q.avg_queue_minutes, q.peak_queue_minutes, q.rides, q.ride_capacity
        FROM silver.park_daily d LEFT JOIN a USING (park_code, business_date) LEFT JOIN q USING (park_code, business_date)"""),
    # ------------------------------------------------------------------ retail facts
    ("gold.fact_retail_sales", "SELECT * FROM silver.retail_line"),
    ("gold.fact_inventory", """
        WITH c AS (SELECT store_code, sku, sum(cost_usd) / nullif(sum(qty), 0) AS unit_cost_usd, sum(net_usd) / nullif(sum(qty), 0) AS unit_price_usd FROM silver.retail_line GROUP BY ALL)
        SELECT v.*, coalesce(c.unit_cost_usd, p.list_price_usd * 0.4) AS unit_cost_usd, coalesce(c.unit_price_usd, p.list_price_usd) AS unit_price_usd,
               v.on_hand_qty * coalesce(c.unit_cost_usd, p.list_price_usd * 0.4) AS stock_value_usd, v.on_hand_qty = 0 AS is_stockout
        FROM silver.inventory_daily v JOIN silver.product p USING (sku) LEFT JOIN c USING (store_code, sku)"""),
    # ------------------------------------------------------------------ spa / marketing / finance facts
    ("gold.fact_spa_appointments", "SELECT a.*, s.service_name, s.service_category FROM silver.spa_appointment a JOIN silver.spa_service s USING (service_code)"),
    ("gold.fact_marketing_campaigns", """
        SELECT d.*, c.channel, c.target_vertical, c.property_id, c.audience_segment FROM silver.campaign_daily d JOIN silver.campaign c USING (campaign_id)"""),
    ("gold.fact_customer_engagement", "SELECT * FROM silver.loyalty_event"),
    ("gold.fact_financial_transactions", "SELECT * FROM silver.gl_transaction"),
    ("gold.fact_budget", "SELECT * FROM silver.budget"),
    ("gold.fact_procurement", "SELECT o.*, s.supplier_name FROM silver.purchase_order o JOIN silver.supplier s USING (supplier_id)"),
    # ------------------------------------------------------------------ marts
    ("gold.mart_daily_revenue", """
        SELECT business_date, property_id, 'Hotels' AS business_vertical, sum(room_revenue_usd) AS revenue_usd, sum(room_revenue_local) AS revenue_local, sum(rooms_sold) AS volume
        FROM gold.fact_room_occupancy WHERE is_actual GROUP BY ALL
        UNION ALL SELECT business_date, property_id, 'Restaurants', sum(net_usd), sum(net_local), sum(covers) FROM gold.fact_restaurant_covers GROUP BY ALL
        UNION ALL SELECT business_date, property_id, 'Waterpark', sum(net_usd), sum(net_local), sum(visitors) FROM gold.fact_waterpark_attendance GROUP BY ALL
        UNION ALL SELECT business_date, property_id, 'Retail', sum(net_usd), sum(net_local), count(DISTINCT txn_id) FROM gold.fact_retail_sales GROUP BY ALL
        UNION ALL SELECT business_date, property_id, 'Spa & Leisure', sum(net_usd), sum(net_local), count(*) FILTER (WHERE status = 'COMPLETED')
                  FROM gold.fact_spa_appointments WHERE status <> 'BOOKED' GROUP BY ALL"""),
    ("gold.mart_executive_daily", f"""
        WITH r AS (PIVOT (SELECT business_date, property_id, business_vertical, revenue_usd FROM gold.mart_daily_revenue)
                   ON business_vertical IN ('Hotels', 'Restaurants', 'Waterpark', 'Retail', 'Spa & Leisure') USING sum(revenue_usd) GROUP BY business_date, property_id),
             o AS (SELECT business_date, property_id, sum(rooms_available) AS rooms_available, sum(rooms_sold) AS rooms_sold FROM gold.fact_room_occupancy WHERE is_actual GROUP BY ALL),
             g AS (SELECT posting_date AS business_date, property_id, sum(amount_usd) FILTER (WHERE account_type = 'REVENUE') AS gl_revenue_usd,
                          sum(amount_usd) FILTER (WHERE account_type = 'EXPENSE') AS opex_usd FROM gold.fact_financial_transactions GROUP BY ALL)
        SELECT o.business_date, o.property_id, o.rooms_available, o.rooms_sold, coalesce(r."Hotels", 0) AS room_revenue_usd, coalesce(r."Restaurants", 0) AS restaurant_revenue_usd,
               coalesce(r."Waterpark", 0) AS waterpark_revenue_usd, coalesce(r."Retail", 0) AS retail_revenue_usd, coalesce(r."Spa & Leisure", 0) AS spa_revenue_usd,
               coalesce(r."Hotels", 0) + coalesce(r."Restaurants", 0) + coalesce(r."Waterpark", 0) + coalesce(r."Retail", 0) + coalesce(r."Spa & Leisure", 0) AS total_revenue_usd,
               coalesce(g.gl_revenue_usd, 0) AS gl_revenue_usd, coalesce(g.opex_usd, 0) AS opex_usd, coalesce(g.gl_revenue_usd, 0) - coalesce(g.opex_usd, 0) AS gop_usd
        FROM o LEFT JOIN r USING (business_date, property_id) LEFT JOIN g USING (business_date, property_id)"""),
    ("gold.mart_guest_360", f"""
        WITH h AS (SELECT guest_key, count(*) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN')) AS stays, sum(nights) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN')) AS nights,
                          sum(room_revenue_usd) FILTER (WHERE status IN ('CHECKED_OUT', 'CHECKED_IN')) AS room_spend_usd, count(*) FILTER (WHERE status = 'CANCELLED') AS cancellations,
                          count(*) AS bookings, count(DISTINCT property_id) AS properties_visited, min(arrival_date) AS first_stay,
                          max(arrival_date) FILTER (WHERE arrival_date <= {A} AND status IN ('CHECKED_OUT', 'CHECKED_IN')) AS last_stay, avg(lead_days) AS avg_lead_days,
                          mode(channel) AS preferred_channel, mode(room_type) AS preferred_room_type, max(arrival_date) FILTER (WHERE status = 'RESERVED') AS next_arrival
                   FROM silver.reservation WHERE guest_key IS NOT NULL GROUP BY 1),
             fb AS (SELECT guest_key, sum(net_usd) AS fb_spend_usd, count(DISTINCT check_id) AS fb_visits, max(business_date) AS last_fb FROM silver.restaurant_line WHERE guest_key IS NOT NULL GROUP BY 1),
             rt AS (SELECT guest_key, sum(net_usd) AS retail_spend_usd, count(DISTINCT txn_id) AS retail_visits, max(business_date) AS last_retail FROM silver.retail_line WHERE guest_key IS NOT NULL GROUP BY 1),
             sp AS (SELECT guest_key, sum(net_usd) AS spa_spend_usd, count(*) FILTER (WHERE status = 'COMPLETED') AS spa_visits, max(business_date) FILTER (WHERE status = 'COMPLETED') AS last_spa
                    FROM silver.spa_appointment WHERE guest_key IS NOT NULL GROUP BY 1),
             wp AS (SELECT guest_key, sum(net_usd) AS waterpark_spend_usd, count(*) AS waterpark_visits, max(visit_date) AS last_wp FROM silver.waterpark_ticket WHERE guest_key IS NOT NULL GROUP BY 1),
             nps AS (SELECT guest_key, avg(nps_score) AS avg_nps FROM silver.guest_feedback GROUP BY 1),
             pts AS (SELECT guest_key, sum(points) FILTER (WHERE event_type = 'EARN') AS points_earned, sum(points) FILTER (WHERE event_type = 'REDEEM') AS points_redeemed
                     FROM silver.loyalty_event WHERE guest_key IS NOT NULL GROUP BY 1)
        SELECT g.guest_key, g.display_name, g.nationality, g.loyalty_tier, g.is_member, g.marketing_consent, coalesce(h.stays, 0) AS stays, coalesce(h.nights, 0) AS nights,
               coalesce(h.bookings, 0) AS bookings, coalesce(h.cancellations, 0) AS cancellations, coalesce(h.properties_visited, 0) AS properties_visited,
               h.first_stay, h.last_stay, h.next_arrival, h.avg_lead_days, h.preferred_channel, h.preferred_room_type,
               coalesce(h.room_spend_usd, 0) AS room_spend_usd, coalesce(fb.fb_spend_usd, 0) AS fb_spend_usd, coalesce(rt.retail_spend_usd, 0) AS retail_spend_usd,
               coalesce(sp.spa_spend_usd, 0) AS spa_spend_usd, coalesce(wp.waterpark_spend_usd, 0) AS waterpark_spend_usd,
               coalesce(h.room_spend_usd, 0) + coalesce(fb.fb_spend_usd, 0) + coalesce(rt.retail_spend_usd, 0) + coalesce(sp.spa_spend_usd, 0) + coalesce(wp.waterpark_spend_usd, 0) AS total_spend_usd,
               coalesce(fb.fb_visits, 0) AS fb_visits, coalesce(rt.retail_visits, 0) AS retail_visits, coalesce(sp.spa_visits, 0) AS spa_visits, coalesce(wp.waterpark_visits, 0) AS waterpark_visits,
               (coalesce(h.stays, 0) > 0)::INT + (coalesce(fb.fb_visits, 0) > 0)::INT + (coalesce(rt.retail_visits, 0) > 0)::INT + (coalesce(sp.spa_visits, 0) > 0)::INT
                   + (coalesce(wp.waterpark_visits, 0) > 0)::INT AS verticals_used,
               greatest(h.last_stay, fb.last_fb, rt.last_retail, sp.last_spa, wp.last_wp) AS last_activity_date, nps.avg_nps,
               coalesce(pts.points_earned, 0) AS points_earned, coalesce(pts.points_redeemed, 0) AS points_redeemed
        FROM silver.guest g LEFT JOIN h USING (guest_key) LEFT JOIN fb USING (guest_key) LEFT JOIN rt USING (guest_key) LEFT JOIN sp USING (guest_key)
        LEFT JOIN wp USING (guest_key) LEFT JOIN nps USING (guest_key) LEFT JOIN pts USING (guest_key)"""),
]

OWNERS = [  # dataset keyword -> (domain, data owner, data steward)
    (("reservation", "room", "feedback"), ("Hotels", "VP Revenue Management", "Hotel Data Steward")),
    (("restaurant", "menu", "table_res", "pos_"), ("Restaurants", "VP Food & Beverage", "F&B Data Steward")),
    (("waterpark", "attraction", "park"), ("Waterpark", "VP Leisure Operations", "Leisure Data Steward")),
    (("retail", "inventory", "product"), ("Retail", "Director of Retail", "Retail Data Steward")),
    (("energy", "meter", "weather", "asset", "device", "work_order", "bms_", "cmms", "iot_", "peak_demand", "water_leak", "vacant_room", "maintenance", "replacement", "capex", "warranty", "contract"),
     ("Engineering & Energy", "Group Director of Engineering", "Engineering Data Steward")),
    (("spa",), ("Spa & Leisure", "Director of Spa & Wellness", "Leisure Data Steward")),
    (("campaign", "customer", "loyalty", "guest", "crm", "marketing"), ("Marketing", "Chief Commercial Officer", "CRM Data Steward")),
    (("gl_", "financ", "budget", "procure", "purchase", "supplier", "fx", "currency"), ("Finance", "Chief Financial Officer", "Finance Data Steward")),
]
PII = {"first_name": "Personal data", "last_name": "Personal data", "email": "Personal data", "display_name": "Personal data (masked)", "email_hash": "Pseudonymised",
       "email_norm": "Personal data", "nationality": "Personal data", "preferences": "Personal data", "guest_key": "Pseudonymised", "employee_name": "Personal data",
       "loyalty_id": "Personal data", "customer_id": "Personal data", "GuestProfileId": "Personal data", "guest_ref": "Personal data", "guest_source_id": "Personal data"}


def build_catalog(con, run_id: str) -> None:
    """Data catalogue + classification (SRS 10.2). Stands in for Microsoft Purview / Fabric OneLake catalogue."""
    rows = con.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema IN ('bronze','silver','gold','ai') ORDER BY 1,2").fetchall()
    out = []
    retention = {"bronze": "7 years (audit / replay)", "silver": "5 years", "gold": "5 years", "ai": "3 years"}
    for schema, name in rows:
        n = con.execute(f'SELECT count(*) FROM {schema}."{name}"').fetchone()[0]
        dom = next((v for keys, v in OWNERS if any(k in name for k in keys)), ("Enterprise", "Chief Data Officer", "Enterprise Data Steward"))
        out.append([schema, name, f"{schema}.{name}", dom[0], dom[1], dom[2], n, retention[schema]])
    con.execute("CREATE OR REPLACE TABLE ops.catalog_tables (layer VARCHAR, table_name VARCHAR, full_name VARCHAR, domain VARCHAR, data_owner VARCHAR, data_steward VARCHAR, row_count BIGINT, retention VARCHAR)")
    con.executemany("INSERT INTO ops.catalog_tables VALUES (?,?,?,?,?,?,?,?)", out)
    con.execute("CREATE OR REPLACE TABLE ops.catalog_columns AS SELECT table_schema AS layer, table_name, column_name, data_type, CAST('Internal' AS VARCHAR) AS classification "
                "FROM information_schema.columns WHERE table_schema IN ('bronze','silver','gold','ai')")
    for col, cls in PII.items():
        con.execute("UPDATE ops.catalog_columns SET classification = ? WHERE column_name = ?", [cls, col])
    con.execute("UPDATE ops.catalog_columns SET classification = 'Confidential (financial)' WHERE classification = 'Internal' AND (column_name LIKE '%amount%' OR column_name LIKE '%budget%' OR column_name LIKE '%cost%')")
    import pandas as pd
    con.register("_kpi_df", pd.DataFrame(load("kpi_definitions.json")["kpis"]))
    con.execute("CREATE OR REPLACE TABLE ops.kpi_glossary AS SELECT * FROM _kpi_df")


def run(con, run_id: str) -> None:
    import pandas as pd

    print("Gold data products")
    for target, sql in TABLES:
        build(con, run_id, target, sql)
    con.register("_kpi_df", pd.DataFrame(load("kpi_definitions.json")["kpis"]))
    build_catalog(con, run_id)
