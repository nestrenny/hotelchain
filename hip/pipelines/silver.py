"""Silver layer: cleansed, conformed, deduplicated enterprise datasets (SRS 4.2).

Typing and format standardisation, harmonisation of the two PMS vendors into one reservation
model, standard property / outlet / room-type / channel identifiers, currency conversion to the
reporting currency, unified guest identity, and SCD2 history for loyalty tier.
"""
from __future__ import annotations

import pandas as pd

from hip.lake import build
from hip.settings import AS_OF, HISTORY_START, load

A = f"DATE '{AS_OF}'"
H = f"DATE '{HISTORY_START}'"


def fx(alias: str, date_expr: str, ccy_expr: str) -> str:
    return f"LEFT JOIN silver.fx_rate {alias} ON {alias}.currency = {ccy_expr} AND {alias}.rate_date = least(CAST({date_expr} AS DATE), {A})"


TABLES: list[tuple[str, str]] = [
    ("silver.fx_rate", """
        SELECT DISTINCT CAST(rate_date AS DATE) AS rate_date, currency, CAST(usd_per_unit AS DOUBLE) AS usd_per_unit FROM bronze.finance_fx_rates"""),
    ("silver.outlet", """
        SELECT o.outlet_code, o.outlet_name, o.property_id, o.outlet_type, o.sub_type, CAST(o.capacity AS INTEGER) AS capacity,
               CAST(o.tables AS INTEGER) AS tables, CAST(o.turns_per_day AS DOUBLE) AS turns_per_day,
               CASE o.outlet_type WHEN 'Restaurant' THEN 'Restaurants' WHEN 'Waterpark' THEN 'Waterpark' WHEN 'Retail Store' THEN 'Retail' ELSE 'Spa & Leisure' END AS business_vertical
        FROM bronze.mdm_outlets o"""),
    ("silver.event_calendar", "SELECT property_id, CAST(event_date AS DATE) AS event_date, min(event_name) AS event_name FROM bronze.mdm_events GROUP BY ALL"),
    ("silver.employee", "SELECT employee_id, employee_name, property_id, department, role FROM bronze.hr_employees"),
    # ---- unified guest identity (lawful matching on normalised e-mail; consent carried from CRM) ----
    ("silver.guest_identity", """
        WITH ids AS (
            SELECT _source_system AS source_system, profile_id AS source_id, max(nullif(lower(trim(email)), '')) AS email_norm FROM bronze.pms_guest_profiles GROUP BY ALL
            UNION ALL
            SELECT 'CRM & Loyalty', customer_id, max(nullif(lower(trim(email)), '')) FROM bronze.crm_customers GROUP BY ALL)
        SELECT source_system, source_id, email_norm, 'G' || substr(md5(coalesce(email_norm, source_id)), 1, 14) AS guest_key, email_norm IS NOT NULL AS matched_on_email FROM ids"""),
    ("silver.customer_history", """
        WITH v AS (SELECT DISTINCT customer_id, loyalty_id, loyalty_tier, CAST(marketing_consent AS INTEGER) AS marketing_consent, country,
                          CAST(created_at AS DATE) AS created_date, CAST(updated_at AS DATE) AS valid_from FROM bronze.crm_customers)
        SELECT v.*, i.guest_key, lead(valid_from) OVER w AS valid_to, lead(valid_from) OVER w IS NULL AS is_current
        FROM v JOIN silver.guest_identity i ON i.source_id = v.customer_id
        WINDOW w AS (PARTITION BY customer_id ORDER BY valid_from, CASE loyalty_tier WHEN 'Silver' THEN 1 WHEN 'Gold' THEN 2 ELSE 3 END)"""),
    ("silver.guest", """
        WITH prof AS (SELECT i.guest_key, any_value(p.first_name) AS first_name, any_value(p.last_name) AS last_name, any_value(p.nationality) AS nationality,
                             any_value(p.preferences) AS preferences
                      FROM bronze.pms_guest_profiles p JOIN silver.guest_identity i ON i.source_id = p.profile_id GROUP BY 1),
             ids AS (SELECT guest_key, any_value(email_norm) AS email_norm, count(DISTINCT source_system) AS source_systems, count(*) AS source_records FROM silver.guest_identity GROUP BY 1),
             crm AS (SELECT guest_key, any_value(customer_id) AS customer_id, any_value(loyalty_id) AS loyalty_id, any_value(loyalty_tier) AS loyalty_tier,
                            max(marketing_consent) AS marketing_consent, any_value(country) AS country, min(created_date) AS member_since
                     FROM silver.customer_history WHERE is_current GROUP BY 1)
        SELECT ids.guest_key, coalesce(prof.first_name, 'Member') || ' ' || coalesce(substr(prof.last_name, 1, 1) || '.', '') AS display_name,
               CASE WHEN ids.email_norm IS NOT NULL THEN sha256(ids.email_norm) END AS email_hash, coalesce(prof.nationality, crm.country) AS nationality,
               prof.preferences, crm.customer_id, crm.loyalty_id, coalesce(crm.loyalty_tier, 'Non-member') AS loyalty_tier, crm.loyalty_id IS NOT NULL AS is_member,
               coalesce(crm.marketing_consent, 0) = 1 AS marketing_consent, crm.member_since, ids.source_systems, ids.source_records
        FROM ids LEFT JOIN prof USING (guest_key) LEFT JOIN crm USING (guest_key)"""),
    # ---- hotels ----
    ("silver.reservation", f"""
        WITH ss AS (
            SELECT ConfirmationNo AS reservation_id, 'StaySuite PMS' AS source_system, PropertyCode AS source_property, GuestProfileId AS guest_source_id,
                   try_strptime(BookingDate, '%Y-%m-%d %H:%M:%S') AS booking_ts, try_cast(ArrivalDate AS DATE) AS arrival_date, try_cast(DepartureDate AS DATE) AS departure_date,
                   RoomTypeCode AS room_type, try_cast(RateAmount AS DOUBLE) * try_cast(Nights AS INTEGER) AS gross_local, coalesce(try_cast(DiscountAmount AS DOUBLE), 0) AS discount_local,
                   CurrencyCode AS currency,
                   CASE Channel WHEN 'WEB' THEN 'Direct Web' WHEN 'OTA' THEN 'OTA' WHEN 'GDS' THEN 'GDS' WHEN 'PHONE' THEN 'Phone' WHEN 'CORPORATE' THEN 'Corporate' WHEN 'WHOLESALE' THEN 'Wholesale' END AS channel,
                   MarketSegment AS segment, Status AS status, try_cast(CancelDate AS DATE) AS cancel_date,
                   try_cast(Adults AS INTEGER) + try_cast(Children AS INTEGER) AS guests, try_strptime(LastModified, '%Y-%m-%d %H:%M:%S') AS modified_ts, _ingested_at
            FROM bronze.pms_reservations_staysuite),
        ik AS (
            SELECT res_id, 'InnKeeper PMS', hotel_id, guest_ref, try_strptime(created_ts, '%Y-%m-%dT%H:%M:%S'), CAST(try_strptime(checkin, '%d/%m/%Y') AS DATE),
                   CAST(try_strptime(checkout, '%d/%m/%Y') AS DATE),
                   CASE room_class WHEN 'DELUXE' THEN 'DLX' WHEN 'PREMIER' THEN 'PRM' WHEN 'SUITE' THEN 'STE' WHEN 'SIGNATURE' THEN 'VIL' END,
                   try_cast(total_room_charge AS DOUBLE), coalesce(try_cast(promo_discount AS DOUBLE), 0), ccy,
                   CASE source WHEN 'W' THEN 'Direct Web' WHEN 'O' THEN 'OTA' WHEN 'G' THEN 'GDS' WHEN 'T' THEN 'Phone' WHEN 'C' THEN 'Corporate' WHEN 'H' THEN 'Wholesale' END,
                   upper(segment[1]) || segment[2:], CASE state WHEN 'B' THEN 'RESERVED' WHEN 'I' THEN 'CHECKED_IN' WHEN 'O' THEN 'CHECKED_OUT' WHEN 'X' THEN 'CANCELLED' WHEN 'N' THEN 'NO_SHOW' END,
                   CAST(try_strptime(cancelled_on, '%d/%m/%Y') AS DATE), try_cast(pax AS INTEGER), try_strptime(updated_ts, '%Y-%m-%dT%H:%M:%S'), _ingested_at
            FROM bronze.pms_reservations_innkeeper),
        u AS (SELECT * FROM ss UNION ALL SELECT * FROM ik),
        d AS (SELECT *, row_number() OVER (PARTITION BY reservation_id ORDER BY modified_ts DESC, _ingested_at DESC) AS rn FROM u)
        SELECT d.reservation_id, d.source_system, p.property_id, i.guest_key, d.booking_ts, d.arrival_date, d.departure_date,
               date_diff('day', d.arrival_date, d.departure_date) AS nights, greatest(date_diff('day', CAST(d.booking_ts AS DATE), d.arrival_date), 0) AS lead_days,
               d.room_type, d.channel, d.segment, d.status, d.cancel_date, d.guests, d.currency, d.gross_local, d.discount_local,
               d.gross_local - d.discount_local AS room_revenue_local, (d.gross_local - d.discount_local) * f.usd_per_unit AS room_revenue_usd,
               d.discount_local * f.usd_per_unit AS discount_usd, d.modified_ts
        FROM d JOIN silver.property p ON p.source_code = d.source_property
        LEFT JOIN silver.guest_identity i ON i.source_id = d.guest_source_id
        {fx('f', 'd.arrival_date', 'd.currency')}
        WHERE d.rn = 1 AND d.arrival_date IS NOT NULL AND d.departure_date > d.arrival_date AND d.room_type IS NOT NULL"""),
    ("silver.room_inventory", """
        SELECT p.property_id, CAST(i.business_date AS DATE) AS business_date,
               CASE i.room_type WHEN 'DELUXE' THEN 'DLX' WHEN 'PREMIER' THEN 'PRM' WHEN 'SUITE' THEN 'STE' WHEN 'SIGNATURE' THEN 'VIL' ELSE i.room_type END AS room_type,
               CAST(i.total_rooms AS INTEGER) AS rooms_total, CAST(i.out_of_order AS INTEGER) AS rooms_out_of_order,
               CAST(i.total_rooms AS INTEGER) - CAST(i.out_of_order AS INTEGER) AS rooms_available
        FROM bronze.pms_room_inventory i JOIN silver.property p ON p.source_code = i.property_code"""),
    ("silver.room_night", f"""
        WITH n AS (SELECT r.*, CAST(unnest(generate_series(arrival_date, departure_date - INTERVAL 1 DAY, INTERVAL 1 DAY)) AS DATE) AS stay_date
                   FROM silver.reservation r WHERE status IN ('CHECKED_OUT', 'CHECKED_IN', 'RESERVED'))
        SELECT reservation_id, property_id, guest_key, room_type, channel, segment, stay_date, status, stay_date <= {A} AS is_actual,
               room_revenue_local / nights AS revenue_local, room_revenue_usd / nights AS revenue_usd
        FROM n WHERE stay_date >= {H} AND ((stay_date <= {A} AND status IN ('CHECKED_OUT', 'CHECKED_IN')) OR (stay_date > {A} AND status IN ('CHECKED_IN', 'RESERVED')))"""),
    ("silver.guest_feedback", """
        SELECT r.reservation_id, r.property_id, r.guest_key, CAST(f.survey_date AS DATE) AS survey_date, CAST(f.nps_score AS INTEGER) AS nps_score,
               CAST(f.overall_satisfaction AS INTEGER) AS overall_satisfaction
        FROM bronze.crm_guest_feedback f JOIN silver.reservation r ON r.reservation_id = f.reservation_ref"""),
    ("silver.guest_complaint", """
        WITH d AS (SELECT *, row_number() OVER (PARTITION BY complaint_id ORDER BY _ingested_at DESC) AS rn FROM bronze.crm_guest_complaints)
        SELECT d.complaint_id, r.reservation_id, r.property_id, r.guest_key, CAST(d.logged_date AS DATE) AS logged_date, d.category, d.severity,
               CAST(d.hours_to_resolve AS DOUBLE) AS hours_to_resolve, CAST(d.resolved AS INTEGER) = 1 AS resolved
        FROM d JOIN silver.reservation r ON r.reservation_id = d.reservation_ref WHERE d.rn = 1"""),
    # ---- restaurants ----
    ("silver.menu_item", "SELECT menu_item_code, outlet_code, item_name, category, CAST(list_price AS DOUBLE) AS list_price_local, currency FROM bronze.pos_menu_items"),
    ("silver.restaurant_line", f"""
        WITH d AS (SELECT *, row_number() OVER (PARTITION BY check_id, line_no ORDER BY _ingested_at) AS rn FROM bronze.pos_transactions)
        SELECT d.check_id, CAST(d.line_no AS INTEGER) AS line_no, d.outlet_code, o.property_id, CAST(d.business_date AS DATE) AS business_date,
               CAST(d.closed_at AS TIMESTAMP) AS closed_ts, hour(CAST(d.closed_at AS TIMESTAMP)) AS hour_of_day,
               CASE WHEN hour(CAST(d.closed_at AS TIMESTAMP)) < 11 THEN 'Breakfast' WHEN hour(CAST(d.closed_at AS TIMESTAMP)) < 15 THEN 'Lunch'
                    WHEN hour(CAST(d.closed_at AS TIMESTAMP)) < 18 THEN 'Afternoon' ELSE 'Dinner' END AS time_slot,
               d.table_id, CAST(d.covers AS INTEGER) AS covers, d.reservation_ref, d.menu_item_code, CAST(d.qty AS INTEGER) AS qty,
               CAST(d.unit_price AS DOUBLE) AS unit_price_local, d.currency, i.guest_key,
               CAST(d.qty AS INTEGER) * CAST(d.unit_price AS DOUBLE) AS gross_local, CAST(d.discount_amount AS DOUBLE) AS discount_local,
               CAST(d.qty AS INTEGER) * CAST(d.unit_price AS DOUBLE) - CAST(d.discount_amount AS DOUBLE) AS net_local,
               CAST(d.qty AS INTEGER) * CAST(d.unit_food_cost AS DOUBLE) AS food_cost_local,
               (CAST(d.qty AS INTEGER) * CAST(d.unit_price AS DOUBLE) - CAST(d.discount_amount AS DOUBLE)) * f.usd_per_unit AS net_usd,
               CAST(d.discount_amount AS DOUBLE) * f.usd_per_unit AS discount_usd,
               CAST(d.qty AS INTEGER) * CAST(d.unit_food_cost AS DOUBLE) * f.usd_per_unit AS food_cost_usd
        FROM d JOIN silver.outlet o ON o.outlet_code = d.outlet_code
        LEFT JOIN silver.guest_identity i ON i.source_id = d.guest_ref
        {fx('f', 'd.business_date', 'd.currency')}
        WHERE d.rn = 1"""),
    ("silver.table_reservation", """
        SELECT t.booking_id, t.outlet_code, o.property_id, CAST(t.reserved_for AS TIMESTAMP) AS reserved_ts, CAST(CAST(t.reserved_for AS TIMESTAMP) AS DATE) AS business_date,
               CAST(t.covers AS INTEGER) AS covers, t.status, t.table_id, i.guest_key
        FROM bronze.restaurant_reservations t JOIN silver.outlet o ON o.outlet_code = t.outlet_code LEFT JOIN silver.guest_identity i ON i.source_id = t.guest_ref"""),
    # ---- waterpark ----
    ("silver.attraction", "SELECT a.attraction_code, a.park_code, o.property_id, a.attraction_name, CAST(a.capacity_per_hour AS INTEGER) AS capacity_per_hour FROM bronze.waterpark_attractions a JOIN silver.outlet o ON o.outlet_code = a.park_code"),
    ("silver.waterpark_ticket", f"""
        SELECT t.ticket_txn_id, t.park_code, o.property_id, CAST(t.purchased_at AS TIMESTAMP) AS purchased_ts, CAST(t.visit_date AS DATE) AS visit_date, t.ticket_category,
               t.sales_channel, CAST(t.qty AS INTEGER) AS visitors, CAST(t.entry_time AS TIMESTAMP) AS entry_ts, CAST(t.exit_time AS TIMESTAMP) AS exit_ts,
               date_diff('minute', CAST(t.entry_time AS TIMESTAMP), CAST(t.exit_time AS TIMESTAMP)) AS dwell_minutes, i.guest_key, t.currency,
               CAST(t.qty AS INTEGER) * CAST(t.unit_price AS DOUBLE) AS gross_local, CAST(t.discount_amount AS DOUBLE) AS discount_local,
               CAST(t.qty AS INTEGER) * CAST(t.unit_price AS DOUBLE) - CAST(t.discount_amount AS DOUBLE) AS net_local,
               (CAST(t.qty AS INTEGER) * CAST(t.unit_price AS DOUBLE) - CAST(t.discount_amount AS DOUBLE)) * f.usd_per_unit AS net_usd,
               CAST(t.discount_amount AS DOUBLE) * f.usd_per_unit AS discount_usd
        FROM bronze.waterpark_tickets t JOIN silver.outlet o ON o.outlet_code = t.park_code
        LEFT JOIN silver.guest_identity i ON i.source_id = t.visitor_ref
        {fx('f', 't.visit_date', 't.currency')}"""),
    ("silver.attraction_hourly", """
        SELECT h.park_code, a.property_id, h.attraction_code, CAST(h.business_date AS DATE) AS business_date, CAST(h.hour AS INTEGER) AS hour_of_day,
               CAST(h.riders AS INTEGER) AS riders, CAST(h.capacity_per_hour AS INTEGER) AS capacity, CAST(h.avg_queue_minutes AS DOUBLE) AS avg_queue_minutes
        FROM bronze.waterpark_attraction_hourly h JOIN silver.attraction a ON a.attraction_code = h.attraction_code"""),
    ("silver.park_daily", """
        SELECT d.park_code, o.property_id, CAST(d.business_date AS DATE) AS business_date, CAST(d.daily_capacity AS INTEGER) AS daily_capacity,
               CAST(d.max_temp_c AS DOUBLE) AS max_temp_c, CAST(d.rain AS INTEGER) = 1 AS rain
        FROM bronze.waterpark_park_daily d JOIN silver.outlet o ON o.outlet_code = d.park_code"""),
    # ---- retail ----
    ("silver.product", "SELECT sku, product_name, category, CAST(list_price_usd AS DOUBLE) AS list_price_usd FROM bronze.retail_products"),
    ("silver.retail_line", f"""
        SELECT r.txn_id, CAST(r.line_no AS INTEGER) AS line_no, r.store_code, o.property_id, CAST(r.sold_at AS TIMESTAMP) AS sold_ts,
               CAST(CAST(r.sold_at AS TIMESTAMP) AS DATE) AS business_date, r.sku, CAST(r.qty AS INTEGER) AS qty, r.sales_channel, i.guest_key, r.currency,
               CAST(r.qty AS INTEGER) * CAST(r.unit_price AS DOUBLE) AS gross_local, CAST(r.discount_amount AS DOUBLE) AS discount_local,
               CAST(r.qty AS INTEGER) * CAST(r.unit_price AS DOUBLE) - CAST(r.discount_amount AS DOUBLE) AS net_local,
               CAST(r.qty AS INTEGER) * CAST(r.unit_cost AS DOUBLE) AS cost_local,
               (CAST(r.qty AS INTEGER) * CAST(r.unit_price AS DOUBLE) - CAST(r.discount_amount AS DOUBLE)) * f.usd_per_unit AS net_usd,
               CAST(r.qty AS INTEGER) * CAST(r.unit_price AS DOUBLE) * f.usd_per_unit AS gross_usd,
               CAST(r.discount_amount AS DOUBLE) * f.usd_per_unit AS discount_usd, CAST(r.qty AS INTEGER) * CAST(r.unit_cost AS DOUBLE) * f.usd_per_unit AS cost_usd
        FROM bronze.retail_transactions r JOIN silver.outlet o ON o.outlet_code = r.store_code
        LEFT JOIN silver.guest_identity i ON i.source_id = r.customer_ref
        {fx('f', 'CAST(r.sold_at AS TIMESTAMP)', 'r.currency')}"""),
    ("silver.inventory_daily", """
        SELECT v.store_code, o.property_id, v.sku, CAST(v.snapshot_date AS DATE) AS snapshot_date, CAST(v.on_hand_qty AS INTEGER) AS on_hand_qty,
               CAST(v.received_qty AS INTEGER) AS received_qty, CAST(v.sold_qty AS INTEGER) AS sold_qty, CAST(v.reorder_point AS INTEGER) AS reorder_point,
               CAST(v.lead_time_days AS INTEGER) AS lead_time_days
        FROM bronze.retail_inventory v JOIN silver.outlet o ON o.outlet_code = v.store_code"""),
    # ---- spa ----
    ("silver.spa_service", "SELECT service_code, service_name, service_category, CAST(duration_minutes AS INTEGER) AS duration_minutes, CAST(list_price_usd AS DOUBLE) AS list_price_usd FROM bronze.spa_services"),
    ("silver.spa_appointment", f"""
        SELECT s.appointment_id, s.spa_code, o.property_id, i.guest_key, s.service_code, s.therapist_id, CAST(s.start_time AS TIMESTAMP) AS start_ts,
               CAST(CAST(s.start_time AS TIMESTAMP) AS DATE) AS business_date, CAST(s.duration_minutes AS INTEGER) AS duration_minutes, s.status,
               CAST(s.booked_at AS TIMESTAMP) AS booked_ts, nullif(s.membership, '') AS membership, s.currency,
               CAST(s.list_price AS DOUBLE) AS list_price_local, CAST(s.discount_amount AS DOUBLE) AS discount_local,
               CASE WHEN s.status = 'COMPLETED' THEN CAST(s.list_price AS DOUBLE) - CAST(s.discount_amount AS DOUBLE) ELSE 0 END AS net_local,
               CASE WHEN s.status = 'COMPLETED' THEN (CAST(s.list_price AS DOUBLE) - CAST(s.discount_amount AS DOUBLE)) * f.usd_per_unit ELSE 0 END AS net_usd,
               (CAST(s.list_price AS DOUBLE) - CAST(s.discount_amount AS DOUBLE)) * f.usd_per_unit AS booked_value_usd
        FROM bronze.spa_appointments s JOIN silver.outlet o ON o.outlet_code = s.spa_code
        LEFT JOIN silver.guest_identity i ON i.source_id = s.guest_ref
        {fx('f', 'CAST(s.start_time AS TIMESTAMP)', 's.currency')}"""),
    # ---- marketing ----
    ("silver.campaign", """
        SELECT campaign_id, campaign_name, channel, target_vertical, property_id, CAST(start_date AS DATE) AS start_date, CAST(end_date AS DATE) AS end_date,
               CAST(budget_usd AS DOUBLE) AS budget_usd, audience_segment FROM bronze.marketing_campaigns"""),
    ("silver.campaign_daily", """
        SELECT campaign_id, CAST(activity_date AS DATE) AS activity_date, CAST(impressions AS BIGINT) AS impressions, CAST(clicks AS BIGINT) AS clicks, CAST(leads AS BIGINT) AS leads,
               CAST(conversions AS BIGINT) AS conversions, CAST(spend_usd AS DOUBLE) AS spend_usd, CAST(attributed_revenue_usd AS DOUBLE) AS attributed_revenue_usd,
               CAST(new_customers AS BIGINT) AS new_customers FROM bronze.marketing_campaign_daily"""),
    ("silver.loyalty_event", """
        SELECT e.event_id, e.loyalty_id, c.guest_key, CAST(e.event_date AS DATE) AS event_date, e.event_type, CAST(e.points AS BIGINT) AS points, e.property_id, e.business_vertical
        FROM bronze.crm_loyalty_events e LEFT JOIN (SELECT DISTINCT loyalty_id, guest_key FROM silver.customer_history WHERE is_current) c USING (loyalty_id)"""),
    # ---- finance ----
    ("silver.supplier", "SELECT supplier_id, supplier_name, category, country FROM bronze.finance_suppliers"),
    ("silver.gl_transaction", f"""
        SELECT g.journal_id, p.property_id, CAST(g.posting_date AS DATE) AS posting_date, g.fiscal_period, g.business_unit AS business_vertical, g.cost_centre,
               g.gl_account, g.gl_account_name, g.account_type, g.currency, CAST(g.amount AS DOUBLE) AS amount_local, CAST(g.amount AS DOUBLE) * f.usd_per_unit AS amount_usd
        FROM bronze.finance_transactions g JOIN silver.property p ON p.erp_code = g.company_code
        {fx('f', 'g.posting_date', 'g.currency')}"""),
    ("silver.budget", f"""
        SELECT p.property_id, b.fiscal_period, CAST(b.fiscal_period || '-01' AS DATE) AS period_start, b.business_unit AS business_vertical, b.cost_centre, b.gl_account,
               b.account_type, b.currency, CAST(b.budget_amount AS DOUBLE) AS budget_local, CAST(b.budget_amount AS DOUBLE) * f.usd_per_unit AS budget_usd
        FROM bronze.finance_budget b JOIN silver.property p ON p.erp_code = b.company_code
        {fx('f', "b.fiscal_period || '-01'", 'b.currency')}"""),
    ("silver.purchase_order", f"""
        SELECT o.po_number, p.property_id, CAST(o.po_date AS DATE) AS po_date, o.supplier_id, o.category, o.item_description, CAST(o.quantity AS INTEGER) AS quantity,
               o.currency, CAST(o.unit_price AS DOUBLE) AS unit_price_local, CAST(o.unit_price AS DOUBLE) * f.usd_per_unit AS unit_price_usd,
               CAST(o.quantity AS INTEGER) * CAST(o.unit_price AS DOUBLE) * f.usd_per_unit AS po_amount_usd, o.invoice_id,
               CAST(o.invoice_amount AS DOUBLE) * f.usd_per_unit AS invoice_amount_usd, CAST(o.promised_days AS INTEGER) AS promised_days,
               CAST(o.delivered_days AS INTEGER) AS delivered_days, CAST(o.delivered_days AS INTEGER) <= CAST(o.promised_days AS INTEGER) AS on_time
        FROM bronze.finance_purchase_orders o JOIN silver.property p ON p.erp_code = o.company_code
        {fx('f', 'o.po_date', 'o.currency')}"""),
]


def run(con, run_id: str) -> None:
    print("Silver standardisation")
    cfg = load("properties.json")
    brands = {b["brand_id"]: b["name"] for b in cfg["brands"]}
    prop_df = pd.DataFrame([{  # property master is governed configuration, not a source feed
        "property_id": p["property_id"], "property_name": p["name"], "short_name": p["short"], "brand_id": p["brand_id"], "brand_name": brands[p["brand_id"]],
        "region": p["region"], "country": p["country"], "city": p["city"], "currency": p["currency"], "timezone": p["timezone"], "pms_system": p["pms"],
        "source_code": p["source_code"], "erp_code": p["erp_code"], "total_rooms": sum(p["rooms"].values()), "has_waterpark": "waterpark" in p} for p in cfg["properties"]])
    rt_df = pd.DataFrame([{"room_type": r["room_type"], "room_type_name": r["name"]} for r in cfg["room_types"]])
    con.register("_prop_df", prop_df)
    con.register("_rt_df", rt_df)
    build(con, run_id, "silver.property", "SELECT * FROM _prop_df")
    build(con, run_id, "silver.room_type", "SELECT * FROM _rt_df")
    for target, sql in TABLES:
        build(con, run_id, target, sql)
