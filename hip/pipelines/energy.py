"""Engineering and energy stage: source extracts -> Bronze -> Silver -> Gold -> models.

    python -m hip.pipelines.run energy

Runs after the core Gold layer, because energy use is modelled against the occupancy already in
the lakehouse and the energy forecast reuses the hotel occupancy forecast.
"""
from __future__ import annotations

from hip.lake import build
from hip.pipelines import bronze
from hip.settings import load

TABLES: list[tuple[str, str]] = [
    ("silver.weather_daily", """
        SELECT property_id, CAST(weather_date AS DATE) AS weather_date, CAST(avg_temp_c AS DOUBLE) AS avg_temp_c, CAST(is_forecast AS INTEGER) = 1 AS is_forecast,
               greatest(CAST(avg_temp_c AS DOUBLE) - 20, 0) AS cooling_degrees, greatest(16 - CAST(avg_temp_c AS DOUBLE), 0) AS heating_degrees
        FROM bronze.bms_weather_daily"""),
    ("silver.energy_daily", "SELECT property_id, CAST(reading_date AS DATE) AS reading_date, end_use, CAST(kwh AS DOUBLE) AS kwh FROM bronze.bms_energy_daily"),
    ("silver.meter_hourly", """
        SELECT property_id, CAST(reading_ts AS TIMESTAMP) AS reading_ts, CAST(CAST(reading_ts AS TIMESTAMP) AS DATE) AS reading_date, hour(CAST(reading_ts AS TIMESTAMP)) AS hour_of_day,
               CAST(kwh AS DOUBLE) AS kwh, CAST(water_m3 AS DOUBLE) AS water_m3 FROM bronze.bms_meter_hourly"""),
    ("silver.asset", """
        SELECT asset_id, property_id, location, device_type, scope, CAST(install_date AS DATE) AS install_date, CAST(expected_life_years AS DOUBLE) AS expected_life_years,
               CAST(replacement_cost_usd AS DOUBLE) AS replacement_cost_usd, CAST(warranty_end AS DATE) AS warranty_end FROM bronze.cmms_assets"""),
    ("silver.device_telemetry", """
        SELECT asset_id, CAST(period_end AS DATE) AS period_end, CAST(runtime_hours AS DOUBLE) AS runtime_hours, CAST(fault_count AS INTEGER) AS fault_count,
               CAST(power_drift_pct AS DOUBLE) AS power_drift_pct, CAST(days_since_service AS DOUBLE) AS days_since_service FROM bronze.iot_device_telemetry"""),
    ("silver.work_order", """
        SELECT w.work_order_id, w.asset_id, a.property_id, a.device_type, CAST(w.completed_date AS DATE) AS completed_date, w.work_type, w.failure_mode,
               CAST(w.downtime_hours AS DOUBLE) AS downtime_hours, CAST(w.cost_usd AS DOUBLE) AS cost_usd, CAST(w.room_out_of_order AS INTEGER) = 1 AS room_out_of_order
        FROM bronze.cmms_work_orders w JOIN silver.asset a USING (asset_id)"""),
    ("silver.asset_contract", """
        SELECT contract_id, property_id, supplier_name, contract_name, contract_kind, covers_device_types, CAST(start_date AS DATE) AS start_date, CAST(end_date AS DATE) AS end_date,
               CAST(annual_value_usd AS DOUBLE) AS annual_value_usd, CAST(auto_renew AS INTEGER) = 1 AS auto_renew, CAST(notice_days AS INTEGER) AS notice_days,
               try_cast(sla_response_hours AS INTEGER) AS sla_response_hours, try_cast(contracted_demand_kw AS DOUBLE) AS contracted_demand_kw FROM bronze.asset_contracts"""),
    ("gold.dim_asset", "SELECT * FROM silver.asset"),
    ("gold.dim_asset_contract", "SELECT * FROM silver.asset_contract"),
    ("gold.fact_device_telemetry", "SELECT t.*, a.property_id, a.device_type FROM silver.device_telemetry t JOIN silver.asset a USING (asset_id)"),
    ("gold.fact_work_orders", "SELECT * FROM silver.work_order"),
    ("gold.fact_energy_daily", "SELECT * FROM silver.energy_daily"),
    ("gold.fact_meter_hourly", "SELECT * FROM silver.meter_hourly"),
    ("gold.fact_weather_daily", "SELECT * FROM silver.weather_daily"),
    ("gold.mart_energy_daily", """
        WITH e AS (SELECT property_id, reading_date, sum(kwh) AS kwh, sum(kwh) FILTER (WHERE end_use = 'Guest room HVAC (vacant rooms)') AS vacant_room_kwh,
                          sum(kwh) FILTER (WHERE end_use LIKE 'Guest room HVAC%') AS guest_room_hvac_kwh FROM silver.energy_daily GROUP BY ALL),
             h AS (SELECT property_id, reading_date, max(kwh) AS peak_kw, sum(water_m3) AS water_m3, avg(water_m3) FILTER (WHERE hour_of_day BETWEEN 2 AND 4) AS night_water_m3_per_hour
                   FROM silver.meter_hourly GROUP BY ALL)
        SELECT e.property_id, e.reading_date, e.kwh, e.vacant_room_kwh, e.guest_room_hvac_kwh, h.peak_kw, h.water_m3, h.night_water_m3_per_hour, w.avg_temp_c, w.cooling_degrees,
               w.heating_degrees, o.rooms_sold, o.rooms_available
        FROM e JOIN h USING (property_id, reading_date) JOIN silver.weather_daily w ON w.property_id = e.property_id AND w.weather_date = e.reading_date
        JOIN gold.mart_executive_daily o ON o.property_id = e.property_id AND o.business_date = e.reading_date"""),
]


def run(con, run_id: str) -> None:
    from hip.ai import energy as energy_ai
    from hip.synth import energy as energy_synth

    energy_synth.main(con)
    print("Engineering and energy: Bronze ingestion")
    for src in load("energy.json")["sources"]:
        # this stage rewrites its own source history, so its feeds are reloaded in full each run
        con.execute(f"DROP TABLE IF EXISTS bronze.{src['table']}")
        con.execute("DELETE FROM ops.watermarks WHERE source_id = ?", [src["id"]])
        bronze.ingest_source(con, src, run_id)
    print("Engineering and energy: Silver and Gold")
    for target, sql in TABLES:
        build(con, run_id, target, sql)
    energy_ai.run(con, run_id)
