"""Bronze ingestion: lands each configured source feed unchanged (all columns as text) with
ingestion metadata. Config-driven, incremental by watermark, schema-evolution tolerant, with
retry and quarantine handling (FR-AZ-003..010, SRS 4.1)."""
from __future__ import annotations

import time
from datetime import datetime

from hip.settings import LANDING, load

MAX_ATTEMPTS = 3


def _q(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def ingest_source(con, src: dict, batch_id: str) -> dict:
    table = f"bronze.{src['table']}"
    files = sorted(str(f).replace("\\", "/") for f in LANDING.glob(src["path"]))
    started = datetime.now()
    log = dict(rows_read=0, rows_loaded=0, rows_quarantined=0, wm_from=None, wm_to=None, status="Succeeded", message="", attempts=0)
    if not files:
        log.update(status="Skipped", message="no files in landing zone")
    for attempt in range(1, MAX_ATTEMPTS + 1 if files else 0):
        log["attempts"] = attempt
        try:
            exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='bronze' AND table_name=?", [src["table"]]).fetchone()[0] > 0
            wm = con.execute("SELECT watermark FROM ops.watermarks WHERE source_id=?", [src["id"]]).fetchone()
            wm = wm[0] if wm and exists else None
            incremental = src["mode"] == "incremental" and src.get("watermark")
            reader = f"read_csv({files!r}, all_varchar=true, union_by_name=true, filename=true, header=true)"
            where = f"WHERE {_q(src['watermark'])} > '{wm}'" if incremental and wm else ""
            con.execute(f"CREATE OR REPLACE TEMP TABLE _incoming AS SELECT * FROM {reader} {where}")
            cols = [r[0] for r in con.execute("DESCRIBE _incoming").fetchall() if r[0] != "filename"]
            log["rows_read"] = con.execute("SELECT count(*) FROM _incoming").fetchone()[0]
            bad = " OR ".join(f"{_q(k)} IS NULL OR trim({_q(k)}) = ''" for k in src["key"])
            con.execute(f"""INSERT INTO ops.quarantine SELECT '{batch_id}', '{src['id']}', 'Missing business key ({", ".join(src['key'])})', filename,
                            CAST(to_json(struct_pack({", ".join(f"{_q(c)} := {_q(c)}" for c in cols)})) AS VARCHAR), now() FROM _incoming WHERE {bad}""")
            log["rows_quarantined"] = con.execute(f"SELECT count(*) FROM _incoming WHERE {bad}").fetchone()[0]
            select = f"""SELECT {", ".join(_q(c) for c in cols)}, '{src['system']}' AS _source_system, '{src['id']}' AS _source_id, '{src['domain']}' AS _business_vertical,
                         filename AS _source_file, '{batch_id}' AS _batch_id, now()::TIMESTAMP AS _ingested_at,
                         md5(concat_ws('|', {", ".join(f"coalesce({_q(c)}, '')" for c in cols)})) AS _row_hash
                         FROM _incoming WHERE NOT ({bad})"""
            if not exists or not incremental:
                con.execute(f"CREATE OR REPLACE TABLE {table} AS {select}")        # first load, or full snapshot refresh
            else:
                have = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
                for c in cols:                                                      # schema evolution: new source columns are added
                    if c not in have:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {_q(c)} VARCHAR")
                con.execute(f"INSERT INTO {table} BY NAME {select}")
            log["rows_loaded"] = log["rows_read"] - log["rows_quarantined"]
            if incremental:
                new_wm = con.execute(f"SELECT max({_q(src['watermark'])}) FROM {table}").fetchone()[0]
                con.execute("INSERT OR REPLACE INTO ops.watermarks VALUES (?,?,now())", [src["id"], new_wm])
                log.update(wm_from=wm, wm_to=new_wm)
            log.update(status="Succeeded", message="" if log["rows_read"] else "no new rows since watermark")
            break
        except Exception as exc:
            log.update(status="Failed", message=str(exc)[:400])
            time.sleep(0.2 * attempt)                                               # back-off before retry
    con.execute("INSERT INTO ops.ingestion_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [batch_id, src["id"], src["system"], src["domain"], table, started, datetime.now(), log["status"], log["attempts"], len(files),
                 log["rows_read"], log["rows_loaded"], log["rows_quarantined"], log["wm_from"], log["wm_to"], log["message"]])
    print(f"  {src['id']:<34}{log['status']:<10}{log['rows_loaded']:>11,} loaded {log['rows_quarantined']:>4} quarantined  {log['message']}")
    return log


def run(con, batch_id: str, only: str | None = None) -> None:
    print("Bronze ingestion")
    failed = []
    for src in load("sources.json")["sources"]:
        if only and src["id"] != only:
            continue
        if ingest_source(con, src, batch_id)["status"] == "Failed":
            failed.append(src["id"])
    if failed:
        print(f"  WARNING: {len(failed)} source(s) failed after {MAX_ATTEMPTS} attempts: {failed}")
