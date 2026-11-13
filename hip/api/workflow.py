"""Human approval, action tracking and audit trail for recommendations and alerts (SRS 7.1 #10,
7.4 C/D, 7.5, 11 Auditability). State lives in a small SQLite database so that it survives
pipeline re-runs: recommendation and alert IDs are deterministic, so a regenerated item keeps
its decision history."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime

from fastapi import HTTPException

from hip.settings import APPDB

DDL = """
CREATE TABLE IF NOT EXISTS rec_state (id TEXT PRIMARY KEY, status TEXT, owner TEXT, decided_by TEXT, decided_at TEXT, comment TEXT, realised_impact_usd REAL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS alert_state (id TEXT PRIMARY KEY, status TEXT, assignee TEXT, escalated_to TEXT, acknowledged_by TEXT, acknowledged_at TEXT, resolved_by TEXT,
                                        resolved_at TEXT, resolution_comment TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, persona_id TEXT, persona TEXT, entity_type TEXT, entity_id TEXT, action TEXT,
                                      from_status TEXT, to_status TEXT, comment TEXT);
"""
REC_FLOW = {  # action -> (allowed from-statuses, to-status)
    "approve": ({"Pending approval"}, "Approved"), "reject": ({"Pending approval"}, "Rejected"), "implement": ({"Approved"}, "Implemented"),
    "reopen": ({"Approved", "Rejected"}, "Pending approval"),
}
ALERT_FLOW = {
    "acknowledge": ({"Open", "Escalated"}, "Acknowledged"), "escalate": ({"Open", "Acknowledged"}, "Escalated"), "resolve": ({"Open", "Acknowledged", "Escalated"}, "Resolved"),
    "reopen": ({"Resolved"}, "Open"),
}


@contextmanager
def db():
    APPDB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(APPDB)
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def states(table: str) -> dict:
    with db() as con:
        return {r["id"]: dict(r) for r in con.execute(f"SELECT * FROM {table}")}


def history(entity_id: str | None = None, limit: int = 200) -> list[dict]:
    with db() as con:
        if entity_id:
            return [dict(r) for r in con.execute("SELECT * FROM audit_log WHERE entity_id = ? ORDER BY id DESC", [entity_id])]
        return [dict(r) for r in con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", [limit])]


def log(con, scope, entity_type: str, entity_id: str, action: str, from_status=None, to_status=None, comment=None) -> None:
    con.execute("INSERT INTO audit_log (at, persona_id, persona, entity_type, entity_id, action, from_status, to_status, comment) VALUES (?,?,?,?,?,?,?,?,?)",
                [datetime.now().isoformat(timespec="seconds"), scope.id, f"{scope.persona['name']} ({scope.persona['title']})", entity_type, entity_id, action, from_status, to_status, comment])


def log_event(scope, entity_type: str, entity_id: str, action: str, comment: str | None = None) -> None:
    with db() as con:
        log(con, scope, entity_type, entity_id, action, comment=comment)


def act_recommendation(scope, rec: dict, action: str, comment: str | None, owner: str | None, realised: float | None) -> None:
    if not scope.sees(rec["property_id"], rec["business_vertical"]):
        raise HTTPException(403, "This recommendation is outside your properties or business verticals")
    now = datetime.now().isoformat(timespec="seconds")
    with db() as con:
        cur = con.execute("SELECT * FROM rec_state WHERE id = ?", [rec["recommendation_id"]]).fetchone()
        status = cur["status"] if cur else "Pending approval"
        if not cur:
            con.execute("INSERT INTO rec_state (id, status, owner, updated_at) VALUES (?,?,?,?)", [rec["recommendation_id"], status, rec["owner_role"], now])
        if action in REC_FLOW:
            if not scope.can_approve:
                raise HTTPException(403, f"{scope.persona['title']} is not authorised to approve or reject recommendations")
            allowed, to = REC_FLOW[action]
            if status not in allowed:
                raise HTTPException(409, f"Cannot {action} a recommendation that is '{status}'")
            if action == "reject" and not (comment or "").strip():
                raise HTTPException(422, "A reason is required to reject a recommendation")
            con.execute("UPDATE rec_state SET status = ?, decided_by = ?, decided_at = ?, comment = coalesce(?, comment), realised_impact_usd = coalesce(?, realised_impact_usd), updated_at = ? WHERE id = ?",
                        [to, f"{scope.persona['name']}, {scope.persona['title']}", now, comment, realised if action == "implement" else None, now, rec["recommendation_id"]])
            log(con, scope, "recommendation", rec["recommendation_id"], action, status, to, comment)
        elif action == "assign":
            if not (owner or "").strip():
                raise HTTPException(422, "An owner is required")
            con.execute("UPDATE rec_state SET owner = ?, updated_at = ? WHERE id = ?", [owner, now, rec["recommendation_id"]])
            log(con, scope, "recommendation", rec["recommendation_id"], "assign", status, status, f"Assigned to {owner}" + (f". {comment}" if comment else ""))
        elif action == "comment":
            if not (comment or "").strip():
                raise HTTPException(422, "A comment is required")
            log(con, scope, "recommendation", rec["recommendation_id"], "comment", status, status, comment)
        else:
            raise HTTPException(400, f"Unknown action '{action}'")


def act_alert(scope, alert: dict, action: str, comment: str | None, assignee: str | None) -> None:
    if not scope.sees(alert["property_id"], alert["business_vertical"]):
        raise HTTPException(403, "This alert is outside your properties or business verticals")
    now = datetime.now().isoformat(timespec="seconds")
    who = f"{scope.persona['name']}, {scope.persona['title']}"
    with db() as con:
        cur = con.execute("SELECT * FROM alert_state WHERE id = ?", [alert["alert_id"]]).fetchone()
        status = cur["status"] if cur else "Open"
        if not cur:
            con.execute("INSERT INTO alert_state (id, status, assignee, updated_at) VALUES (?,?,?,?)", [alert["alert_id"], status, alert["owner_role"], now])
        if action in ALERT_FLOW:
            allowed, to = ALERT_FLOW[action]
            if status not in allowed:
                raise HTTPException(409, f"Cannot {action} an alert that is '{status}'")
            if action == "resolve" and not (comment or "").strip():
                raise HTTPException(422, "A resolution comment is required")
            if action == "acknowledge":
                con.execute("UPDATE alert_state SET status = ?, acknowledged_by = ?, acknowledged_at = ?, updated_at = ? WHERE id = ?", [to, who, now, now, alert["alert_id"]])
            elif action == "escalate":
                target = "Chief Operating Officer" if alert["property_id"] == "ALL" or scope.enterprise else "Regional Director"
                con.execute("UPDATE alert_state SET status = ?, escalated_to = ?, updated_at = ? WHERE id = ?", [to, target, now, alert["alert_id"]])
                comment = f"Escalated to {target}" + (f". {comment}" if comment else "")
            elif action == "resolve":
                con.execute("UPDATE alert_state SET status = ?, resolved_by = ?, resolved_at = ?, resolution_comment = ?, updated_at = ? WHERE id = ?", [to, who, now, comment, now, alert["alert_id"]])
            else:
                con.execute("UPDATE alert_state SET status = ?, resolved_by = NULL, resolved_at = NULL, updated_at = ? WHERE id = ?", [to, now, alert["alert_id"]])
            log(con, scope, "alert", alert["alert_id"], action, status, to, comment)
        elif action == "assign":
            if not (assignee or "").strip():
                raise HTTPException(422, "An assignee is required")
            con.execute("UPDATE alert_state SET assignee = ?, updated_at = ? WHERE id = ?", [assignee, now, alert["alert_id"]])
            log(con, scope, "alert", alert["alert_id"], "assign", status, status, f"Assigned to {assignee}" + (f". {comment}" if comment else ""))
        elif action == "comment":
            if not (comment or "").strip():
                raise HTTPException(422, "A comment is required")
            log(con, scope, "alert", alert["alert_id"], "comment", status, status, comment)
        else:
            raise HTTPException(400, f"Unknown action '{action}'")
