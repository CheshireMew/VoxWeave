from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .database import Database, utc_now
from .protocol import OperationError
from .realtime import RealtimeSessionManager


class RealtimeSceneRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        result = Database.decode_json_row(row, ("settings_json", "hotkeys_json"))
        result["archived"] = bool(result["archived"])
        return result

    def get(self, scene_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM realtime_scenes WHERE id=?", (scene_id,)
        )
        if not row:
            raise LookupError(f"realtime scene not found: {scene_id}")
        return self._decode(row)

    def list(self, include_archived: bool = False) -> dict[str, Any]:
        where = "" if include_archived else "WHERE archived=0"
        rows = self.database.fetch_all(
            f"SELECT * FROM realtime_scenes {where} ORDER BY archived,name,id"  # noqa: S608
        )
        return {"items": [self._decode(row) for row in rows]}

    def create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(uuid.uuid4())
        now = utc_now()
        try:
            with self.database.connect() as db:
                db.execute(
                    "INSERT INTO realtime_scenes("
                    "id,name,settings_json,hotkeys_json,archived,revision,created_at,updated_at) "
                    "VALUES(?,?,?,?,0,1,?,?)",
                    (
                        scene_id,
                        arguments["name"],
                        json.dumps(arguments["settings"], ensure_ascii=False),
                        json.dumps(arguments["hotkeys"], ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise OperationError("scene_name_conflict", "scene name already exists") from error
        return self.get(scene_id)

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        current = self.get(arguments["scene_id"])
        expected = int(arguments["expected_revision"])
        if current["revision"] != expected:
            raise OperationError(
                "revision_conflict",
                f"scene revision conflict: expected {expected}, current {current['revision']}",
            )
        updates = {
            key: arguments[key]
            for key in ("name", "settings", "hotkeys")
            if arguments.get(key) is not None
        }
        columns = []
        values: list[Any] = []
        for key, value in updates.items():
            column = f"{key}_json" if key in {"settings", "hotkeys"} else key
            columns.append(f"{column}=?")
            values.append(
                json.dumps(value, ensure_ascii=False)
                if key in {"settings", "hotkeys"}
                else value
            )
        columns.extend(("revision=revision+1", "updated_at=?"))
        values.extend((utc_now(), current["id"], expected))
        try:
            with self.database.connect() as db:
                cursor = db.execute(
                    f"UPDATE realtime_scenes SET {','.join(columns)} "  # noqa: S608
                    "WHERE id=? AND revision=?",
                    tuple(values),
                )
                if cursor.rowcount != 1:
                    raise OperationError("revision_conflict", "scene changed concurrently")
        except sqlite3.IntegrityError as error:
            raise OperationError("scene_name_conflict", "scene name already exists") from error
        return self.get(current["id"])

    def archive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        current = self.get(arguments["scene_id"])
        expected = int(arguments["expected_revision"])
        with self.database.connect() as db:
            cursor = db.execute(
                "UPDATE realtime_scenes SET archived=?,revision=revision+1,updated_at=? "
                "WHERE id=? AND revision=?",
                (int(arguments["archived"]), utc_now(), current["id"], expected),
            )
            if cursor.rowcount != 1:
                raise OperationError("revision_conflict", "scene changed concurrently")
        return self.get(current["id"])


class RealtimeWorkspaceService:
    def __init__(self, database: Database, realtime: RealtimeSessionManager) -> None:
        self.repository = RealtimeSceneRepository(database)
        self.realtime = realtime

    def routing(self) -> dict[str, Any]:
        devices = self.realtime.devices()["devices"]
        routes = []
        products: set[str] = set()
        input_candidate = None
        output_candidate = None
        for device in devices:
            name = str(device["name"])
            folded = name.casefold()
            kind = "direct"
            if "voicemeeter" in folded:
                kind = "mixer"
                products.add("Voicemeeter")
            elif any(token in folded for token in ("vb-audio", "cable input", "cable output")):
                products.add("VB-CABLE")
                if int(device["input_channels"]) > 0:
                    kind = "virtual_input"
                    input_candidate = input_candidate or int(device["id"])
                if int(device["output_channels"]) > 0:
                    kind = "virtual_output"
                    output_candidate = output_candidate or int(device["id"])
            routes.append(
                {
                    "kind": kind,
                    "device_id": int(device["id"]),
                    "name": name,
                    "hostapi": str(device["hostapi"]),
                    "input_channels": int(device["input_channels"]),
                    "output_channels": int(device["output_channels"]),
                }
            )
        return {
            "virtual_audio_available": bool(products),
            "detected_products": sorted(products),
            "routes": routes,
            "recommended_input_device": input_candidate,
            "recommended_output_device": output_candidate,
            "instructions": [
                "Select a physical microphone as VoxWeave input.",
                "Select a virtual cable playback endpoint as VoxWeave output.",
                "Select the matching virtual cable recording endpoint in the target app.",
            ],
        }

    def apply(self, arguments: dict[str, Any]) -> dict[str, Any]:
        scene = self.repository.get(arguments["scene_id"])
        if scene["archived"]:
            raise OperationError("scene_archived", "archived scene cannot be applied")
        devices = self.realtime.devices()["devices"]
        settings = dict(scene["settings"])

        def resolve(name: str, channel: str) -> int:
            count_key = f"{channel}_channels"
            matches = [
                item
                for item in devices
                if str(item["name"]) == name
                and int(item[count_key]) > 0
                and (
                    not settings.get("hostapi")
                    or str(item["hostapi"]) == settings["hostapi"]
                )
            ]
            if not matches:
                raise OperationError("audio_route_missing", f"scene device is unavailable: {name}")
            return int(matches[0]["id"])

        start_arguments = {
            **settings,
            "input_device": resolve(settings["input_device"], "input"),
            "output_device": resolve(settings["output_device"], "output"),
        }
        start_arguments.pop("hostapi", None)
        if arguments.get("recording") is not None:
            start_arguments["recording"] = bool(arguments["recording"])
        if not arguments.get("start", True):
            return self.realtime.prepare(start_arguments)
        return self.realtime.start(start_arguments)
