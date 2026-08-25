from __future__ import annotations

import json
import sqlite3
from typing import Any

from .database import Database, utc_now

TERMINAL_STATES = {"stopped", "failed", "interrupted"}


class RealtimeRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _insert_event(
        db: sqlite3.Connection,
        session_id: str,
        state: str,
        stage: str | None,
        detail: str | None,
        metrics: dict[str, Any] | None,
    ) -> None:
        db.execute(
            "INSERT INTO realtime_events("
            "session_id,state,stage,detail,metrics_json,created_at) VALUES(?,?,?,?,?,?)",
            (
                session_id,
                state,
                stage,
                detail,
                json.dumps(metrics, ensure_ascii=False) if metrics is not None else None,
                utc_now(),
            ),
        )

    def recover_interrupted(self) -> None:
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT id FROM realtime_sessions "
                "WHERE state IN ('starting','running','stopping')"
            ).fetchall()
            now = utc_now()
            for row in rows:
                db.execute(
                    "UPDATE realtime_sessions SET state='interrupted',stage='service_restart',"
                    "error_type='service_restart',error='service stopped during realtime session',"
                    "stopped_at=?,updated_at=? WHERE id=?",
                    (now, now, row["id"]),
                )
                self._insert_event(
                    db,
                    row["id"],
                    "interrupted",
                    "service_restart",
                    "service stopped during realtime session",
                    None,
                )

    def active(self) -> dict[str, Any] | None:
        return self.database.fetch_one(
            "SELECT id FROM realtime_sessions "
            "WHERE state IN ('starting','running','stopping') "
            "ORDER BY created_at DESC LIMIT 1"
        )

    def create(
        self,
        session_id: str,
        model: dict[str, Any],
        arguments: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO realtime_sessions("
                "id,model_id,model_sha256,index_sha256,arguments_json,state,stage,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    model["id"],
                    model["model_sha256"],
                    model.get("index_sha256"),
                    json.dumps(arguments, ensure_ascii=False),
                    "starting",
                    "waiting_for_worker",
                    now,
                    now,
                ),
            )
            self._insert_event(
                db,
                session_id,
                "starting",
                "waiting_for_worker",
                "waiting for resident RVC worker",
                None,
            )

    def transition(
        self,
        session_id: str,
        *,
        state: str,
        stage: str,
        detail: str | None,
        metrics: dict[str, Any] | None,
        error_type: str | None,
        error: str | None,
    ) -> None:
        with self.database.connect() as db:
            current = db.execute(
                "SELECT state,started_at FROM realtime_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not current or current["state"] in TERMINAL_STATES:
                return
            if current["state"] == "stopping" and state in {"starting", "running"}:
                return
            now = utc_now()
            started_at = now if state == "running" and not current["started_at"] else None
            stopped_at = now if state in TERMINAL_STATES else None
            db.execute(
                "UPDATE realtime_sessions SET state=?,stage=?,"
                "metrics_json=COALESCE(?,metrics_json),error_type=?,error=?,"
                "started_at=COALESCE(started_at,?),stopped_at=COALESCE(stopped_at,?),"
                "updated_at=? WHERE id=?",
                (
                    state,
                    stage,
                    json.dumps(metrics, ensure_ascii=False) if metrics is not None else None,
                    error_type,
                    error,
                    started_at,
                    stopped_at,
                    now,
                    session_id,
                ),
            )
            self._insert_event(db, session_id, state, stage, detail or error, metrics)

    def heartbeat(self, session_id: str, metrics: dict[str, Any]) -> None:
        with self.database.connect() as db:
            current = db.execute(
                "SELECT state,stage,metrics_json FROM realtime_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if not current or current["state"] in TERMINAL_STATES:
                return
            stage = "overloaded" if metrics.get("overloaded") else "streaming"
            if current["state"] == "stopping":
                stage = "stopping"
            merged = json.loads(current["metrics_json"] or "{}")
            merged.update(metrics)
            db.execute(
                "UPDATE realtime_sessions SET stage=?,metrics_json=?,updated_at=? WHERE id=?",
                (stage, json.dumps(merged, ensure_ascii=False), utc_now(), session_id),
            )
            if stage != current["stage"]:
                self._insert_event(
                    db,
                    session_id,
                    current["state"],
                    stage,
                    "realtime performance state changed",
                    merged,
                )

    def update_control(self, session_id: str, changes: dict[str, Any]) -> None:
        with self.database.connect() as db:
            current = db.execute(
                "SELECT state,metrics_json FROM realtime_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if not current or current["state"] not in {"starting", "running"}:
                raise RuntimeError("realtime session is not active")
            metrics = json.loads(current["metrics_json"] or "{}")
            metrics.update(changes)
            db.execute(
                "UPDATE realtime_sessions SET metrics_json=?,updated_at=? WHERE id=?",
                (json.dumps(metrics, ensure_ascii=False), utc_now(), session_id),
            )
            self._insert_event(
                db,
                session_id,
                current["state"],
                "control",
                "realtime controls changed",
                metrics,
            )

    def get(self, session_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM realtime_sessions WHERE id=?", (session_id,)
        )
        if not row:
            raise LookupError(f"realtime session not found: {session_id}")
        result = Database.decode_json_row(row, ("arguments_json", "metrics_json"))
        result["metrics"] = result.get("metrics") or {}
        result["session_id"] = result["id"]
        return result

    def latest_id(self) -> str | None:
        row = self.database.fetch_one(
            "SELECT id FROM realtime_sessions ORDER BY created_at DESC LIMIT 1"
        )
        return str(row["id"]) if row else None

    def events(self, session_id: str, after_id: int) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT * FROM realtime_events WHERE session_id=? AND id>? ORDER BY id",
            (session_id, after_id),
        )
        results = []
        for row in rows:
            result = Database.decode_json_row(row, ("metrics_json",))
            result["metrics"] = result.get("metrics") or {}
            results.append(result)
        return results
