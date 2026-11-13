"""Data quality rules and cross-system reconciliation (SRS 2.1, 4.2, 16.3)."""
from __future__ import annotations

from datetime import datetime

from hip.settings import AS_OF, HISTORY_START

A = f"DATE '{AS_OF}'"
H = f"DATE '{HISTORY_START}'"

# rule_id, layer, dataset, description, severity, failed-count SQL, total-count SQL
RULES = [
    ("DQ-BRZ-001", "bronze", "ops.quarantine", "Source rows quarantined for a missing business key", "Warning",
     "SELECT count(*) FROM ops.quarantine", "SELECT sum(rows_read) FROM ops.ingestion_log"),
    ("DQ-SLV-001", "silver", "silver.reservation", "Reservations rejected for invalid stay dates or unmapped room type", "Warning",
     """SELECT (SELECT count(DISTINCT ConfirmationNo) FROM bronze.pms_reservations_staysuite) + (SELECT count(DISTINCT res_id) FROM bronze.pms_reservations_innkeeper)
               - (SELECT count(*) FROM silver.reservation)""",
     "SELECT (SELECT count(DISTINCT ConfirmationNo) FROM bronze.pms_reservations_staysuite) + (SELECT count(DISTINCT res_id) FROM bronze.pms_reservations_innkeeper)"),
    ("DQ-SLV-002", "silver", "silver.reservation", "Reservation ID is unique after CDC de-duplication", "Critical",
     "SELECT count(*) - count(DISTINCT reservation_id) FROM silver.reservation", "SELECT count(*) FROM silver.reservation"),
    ("DQ-SLV-003", "silver", "silver.reservation", "Booking channel and status map to the enterprise standard", "Critical",
     "SELECT count(*) FROM silver.reservation WHERE channel IS NULL OR status IS NULL", "SELECT count(*) FROM silver.reservation"),
    ("DQ-SLV-004", "silver", "silver.reservation", "Reservation resolves to a unified guest identity", "Warning",
     "SELECT count(*) FROM silver.reservation WHERE guest_key IS NULL", "SELECT count(*) FROM silver.reservation"),
    ("DQ-SLV-005", "silver", "silver.guest_identity", "Guest profile matched on a verified e-mail (identity resolution coverage)", "Info",
     "SELECT count(*) FROM silver.guest_identity WHERE NOT matched_on_email", "SELECT count(*) FROM silver.guest_identity"),
    ("DQ-SLV-006", "silver", "silver.restaurant_line", "Duplicate POS lines removed during standardisation", "Info",
     "SELECT (SELECT count(*) FROM bronze.pos_transactions) - (SELECT count(*) FROM silver.restaurant_line)", "SELECT count(*) FROM bronze.pos_transactions"),
    ("DQ-SLV-007", "silver", "silver.restaurant_line", "POS line has a positive price and a known menu item", "Critical",
     "SELECT count(*) FROM silver.restaurant_line l LEFT JOIN silver.menu_item m USING (menu_item_code) WHERE l.unit_price_local <= 0 OR m.menu_item_code IS NULL",
     "SELECT count(*) FROM silver.restaurant_line"),
    ("DQ-SLV-008", "silver", "silver.retail_line", "Retail line references a known SKU", "Critical",
     "SELECT count(*) FROM silver.retail_line l LEFT JOIN silver.product p USING (sku) WHERE p.sku IS NULL", "SELECT count(*) FROM silver.retail_line"),
    ("DQ-SLV-009", "silver", "silver.spa_appointment", "Spa appointment references a known therapist", "Warning",
     "SELECT count(*) FROM silver.spa_appointment a LEFT JOIN silver.employee e ON e.employee_id = a.therapist_id WHERE e.employee_id IS NULL", "SELECT count(*) FROM silver.spa_appointment"),
    ("DQ-SLV-010", "silver", "silver.gl_transaction", "Every amount converts to the reporting currency (FX rate available)", "Critical",
     "SELECT count(*) FROM silver.gl_transaction WHERE amount_usd IS NULL", "SELECT count(*) FROM silver.gl_transaction"),
    ("DQ-GLD-001", "gold", "gold.fact_room_occupancy", "Rooms sold never exceed available sellable rooms", "Critical",
     "SELECT count(*) FROM gold.fact_room_occupancy WHERE rooms_sold > rooms_available", "SELECT count(*) FROM gold.fact_room_occupancy WHERE is_actual"),
    ("DQ-GLD-002", "gold", "gold.mart_daily_revenue", "Every revenue vertical is loaded through the last closed business date", "Critical",
     f"SELECT count(*) FROM (SELECT business_vertical, max(business_date) AS d FROM gold.mart_daily_revenue GROUP BY 1) WHERE d < {A}",
     "SELECT count(DISTINCT business_vertical) FROM gold.mart_daily_revenue"),
    ("DQ-GLD-003", "gold", "gold.fact_inventory", "Inventory on hand is never negative", "Critical",
     "SELECT count(*) FROM gold.fact_inventory WHERE on_hand_qty < 0", "SELECT count(*) FROM gold.fact_inventory"),
    ("DQ-GLD-004", "gold", "gold.fact_procurement", "Invoice amount matches purchase order amount (3-way match tolerance 2%)", "Warning",
     "SELECT count(*) FROM gold.fact_procurement WHERE abs(invoice_amount_usd - po_amount_usd) > 0.02 * po_amount_usd", "SELECT count(*) FROM gold.fact_procurement"),
]


def run(con, run_id: str) -> None:
    print("Data quality and reconciliation")
    now = datetime.now()
    # operational revenue vs general ledger, per property / vertical / month, in local currency
    con.execute(f"""
        CREATE OR REPLACE TABLE ops.reconciliation AS
        WITH op AS (SELECT property_id, business_vertical, strftime(business_date, '%Y-%m') AS fiscal_period, sum(revenue_local) AS operational_local
                    FROM gold.mart_daily_revenue WHERE business_date BETWEEN {H} AND {A} GROUP BY ALL),
             gl AS (SELECT property_id, business_vertical, fiscal_period, sum(amount_local) AS ledger_local FROM gold.fact_financial_transactions WHERE account_type = 'REVENUE' GROUP BY ALL)
        SELECT coalesce(op.property_id, gl.property_id) AS property_id, coalesce(op.business_vertical, gl.business_vertical) AS business_vertical,
               coalesce(op.fiscal_period, gl.fiscal_period) AS fiscal_period, coalesce(operational_local, 0) AS operational_local, coalesce(ledger_local, 0) AS ledger_local,
               coalesce(operational_local, 0) - coalesce(ledger_local, 0) AS difference_local,
               (coalesce(operational_local, 0) - coalesce(ledger_local, 0)) / nullif(coalesce(operational_local, 0), 0) AS difference_pct,
               CASE WHEN abs(coalesce(operational_local, 0) - coalesce(ledger_local, 0)) <= 0.005 * greatest(coalesce(operational_local, 0), 1) THEN 'Reconciled' ELSE 'Break' END AS status
        FROM op FULL JOIN gl USING (property_id, business_vertical, fiscal_period)""")
    rules = list(RULES)
    for v in ["Hotels", "Restaurants", "Waterpark", "Retail", "Spa & Leisure"]:
        rules.append((f"DQ-REC-{v[:3].upper()}", "gold", "ops.reconciliation", f"{v} operational revenue reconciles to the general ledger (0.5% tolerance, by property-month)", "Critical",
                      f"SELECT count(*) FROM ops.reconciliation WHERE business_vertical = '{v}' AND status = 'Break'", f"SELECT count(*) FROM ops.reconciliation WHERE business_vertical = '{v}'"))
    out = []
    for rid, layer, dataset, desc, sev, f_sql, t_sql in rules:
        failed = int(con.execute(f_sql).fetchone()[0] or 0)
        total = int(con.execute(t_sql).fetchone()[0] or 0)
        rate = 1 - failed / total if total else 1.0
        status = "Passed" if failed == 0 else ("Monitored" if sev == "Info" else "Failed" if sev == "Critical" else "Warning")
        detail = ""
        if rid.startswith("DQ-REC") and failed:
            r = con.execute(f"SELECT property_id, fiscal_period, difference_local, difference_pct FROM ops.reconciliation WHERE business_vertical = ? AND status = 'Break' ORDER BY abs(difference_local) DESC LIMIT 1",
                            [desc.split(" operational")[0]]).fetchone()
            detail = f"Largest break: {r[0]} {r[1]}, operational exceeds ledger by {r[2]:,.0f} local ({r[3]:.1%})"
        out.append([run_id, now, rid, layer, dataset, desc, sev, failed, total, rate, status, detail])
        print(f"  {rid:<12}{status:<10}{failed:>9,} / {total:<11,} {desc}")
    con.executemany("INSERT INTO ops.dq_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", out)
