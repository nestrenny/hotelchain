"""Lakehouse helpers: connection, operational metadata tables, and a build() wrapper that
records run statistics and lineage for every Silver/Gold table (SRS 4.2, 10.2, 11 Auditability)."""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime

import duckdb

from hip.settings import LAKE

OPS_DDL = """
CREATE SCHEMA IF NOT EXISTS bronze; CREATE SCHEMA IF NOT EXISTS silver; CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS ops; CREATE SCHEMA IF NOT EXISTS ai;
CREATE TABLE IF NOT EXISTS ops.ingestion_log (batch_id VARCHAR, source_id VARCHAR, source_system VARCHAR, domain VARCHAR, target_table VARCHAR,
    started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR, attempts INTEGER, files INTEGER, rows_read BIGINT, rows_loaded BIGINT,
    rows_quarantined BIGINT, watermark_from VARCHAR, watermark_to VARCHAR, message VARCHAR);
CREATE TABLE IF NOT EXISTS ops.watermarks (source_id VARCHAR PRIMARY KEY, watermark VARCHAR, updated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS ops.quarantine (batch_id VARCHAR, source_id VARCHAR, reason VARCHAR, source_file VARCHAR, payload VARCHAR, quarantined_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (run_id VARCHAR, layer VARCHAR, target VARCHAR, started_at TIMESTAMP, seconds DOUBLE, row_count BIGINT, status VARCHAR, message VARCHAR);
CREATE TABLE IF NOT EXISTS ops.lineage (target VARCHAR, source VARCHAR, layer VARCHAR, updated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS ops.dq_results (run_id VARCHAR, run_at TIMESTAMP, rule_id VARCHAR, layer VARCHAR, dataset VARCHAR, description VARCHAR,
    severity VARCHAR, failed BIGINT, total BIGINT, pass_rate DOUBLE, status VARCHAR, detail VARCHAR);
"""


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    LAKE.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(LAKE), read_only=read_only)
    if not read_only:
        con.execute(OPS_DDL)
    return con


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]


def build(con, run_id: str, target: str, sql: str) -> int:
    """CREATE OR REPLACE a table, logging duration, row count and upstream lineage."""
    layer = target.split(".")[0]
    t0, started = time.time(), datetime.now()
    try:
        con.execute(f"CREATE OR REPLACE TABLE {target} AS {sql}")
        n = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
        status, msg = "Succeeded", ""
    except Exception as exc:  # recorded, then re-raised so the orchestrator stops
        n, status, msg = 0, "Failed", str(exc)[:500]
    con.execute("INSERT INTO ops.pipeline_runs VALUES (?,?,?,?,?,?,?,?)", [run_id, layer, target, started, round(time.time() - t0, 3), n, status, msg])
    if status == "Failed":
        raise RuntimeError(f"{target}: {msg}")
    sources = sorted(set(re.findall(r"\b(?:bronze|silver|gold|ai)\.[a-z_0-9]+", sql)) - {target})
    con.execute("DELETE FROM ops.lineage WHERE target = ?", [target])
    con.executemany("INSERT INTO ops.lineage VALUES (?,?,?,?)", [[target, s, layer, started] for s in sources] or [[target, "config", layer, started]])
    print(f"  {target:<44}{n:>11,} rows  {time.time() - t0:5.1f}s")
    return n
