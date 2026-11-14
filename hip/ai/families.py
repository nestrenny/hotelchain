"""Forecast families: what is forecast and the Gold query behind each series.

Kept free of machine-learning imports so the web API can read it without loading scikit-learn."""

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
