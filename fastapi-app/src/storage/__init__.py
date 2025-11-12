"""Simple file-based storage backend for training metadata."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, Iterable, List, Optional
from uuid import uuid4

from src.models import LogEntry, Project, ProjectCreate, ProjectDetail, RunDetail, RunStatus
from src.utils.filesystem import ensure_directories


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _deserialize_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


class DatabaseStorage:
    """A minimal JSON file-backed storage for projects and runs."""

    def __init__(self, db_path: Path):
        self._path = Path(db_path)
        ensure_directories(self._path.parent)
        if not self._path.exists():
            self._path.write_text(
                json.dumps({"projects": {}, "runs": {}, "project_runs": {}}, indent=2),
                encoding="utf-8",
            )
        self._lock = Lock()

    # Internal helpers --------------------------------------------------
    def _load(self) -> Dict[str, Dict[str, dict]]:
        content = self._path.read_text(encoding="utf-8")
        if not content:
            return {"projects": {}, "runs": {}, "project_runs": {}}
        return json.loads(content)

    def _save(self, data: Dict[str, Dict[str, dict]]) -> None:
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _deserialize_project(self, record: dict, runs: Optional[List[RunDetail]] = None) -> ProjectDetail:
        return ProjectDetail(
            id=record["id"],
            name=record["name"],
            dataset_name=record["dataset_name"],
            training_yaml_name=record["training_yaml_name"],
            description=record.get("description"),
            created_at=_deserialize_datetime(record["created_at"]),
            updated_at=_deserialize_datetime(record["updated_at"]),
            runs=runs or [],
        )

    def _deserialize_project_summary(self, record: dict) -> Project:
        return Project(
            id=record["id"],
            name=record["name"],
            dataset_name=record["dataset_name"],
            training_yaml_name=record["training_yaml_name"],
            description=record.get("description"),
            created_at=_deserialize_datetime(record["created_at"]),
            updated_at=_deserialize_datetime(record["updated_at"]),
        )

    def _deserialize_run(self, record: dict) -> RunDetail:
        logs = [
            LogEntry(
                timestamp=_deserialize_datetime(entry["timestamp"]),
                level=entry["level"],
                message=entry["message"],
            )
            for entry in record.get("logs", [])
        ]
        return RunDetail(
            id=record["id"],
            project_id=record["project_id"],
            status=RunStatus(record["status"]),
            progress=record.get("progress", 0.0),
            start_command=record["start_command"],
            created_at=_deserialize_datetime(record["created_at"]),
            updated_at=_deserialize_datetime(record["updated_at"]),
            logs=logs,
        )

    # Project operations ------------------------------------------------
    def create_project(self, payload: ProjectCreate) -> ProjectDetail:
        now = _now()
        with self._lock:
            data = self._load()
            project_id = str(uuid4())
            project_record = {
                "id": project_id,
                "name": payload.name,
                "dataset_name": payload.dataset_name,
                "training_yaml_name": payload.training_yaml_name,
                "description": payload.description,
                "created_at": _serialize_datetime(now),
                "updated_at": _serialize_datetime(now),
            }
            data["projects"][project_id] = project_record
            data.setdefault("project_runs", {})[project_id] = []
            self._save(data)
        return self._deserialize_project(project_record, runs=[])

    def list_projects(self) -> Iterable[Project]:
        data = self._load()
        for record in data.get("projects", {}).values():
            yield self._deserialize_project_summary(record)

    def get_project(self, project_id: str) -> Optional[ProjectDetail]:
        data = self._load()
        record = data.get("projects", {}).get(project_id)
        if not record:
            return None
        run_ids = data.get("project_runs", {}).get(project_id, [])
        runs = [self._deserialize_run(data["runs"][rid]) for rid in run_ids if rid in data.get("runs", {})]
        return self._deserialize_project(record, runs)

    def get_project_by_name(self, name: str) -> Optional[ProjectDetail]:
        data = self._load()
        for record in data.get("projects", {}).values():
            if record.get("name") == name:
                project_id = record["id"]
                run_ids = data.get("project_runs", {}).get(project_id, [])
                runs = [
                    self._deserialize_run(data["runs"][rid])
                    for rid in run_ids
                    if rid in data.get("runs", {})
                ]
                return self._deserialize_project(record, runs)
        return None

    # Run operations -----------------------------------------------------
    def create_run(self, project_id: str, start_command: str) -> RunDetail:
        now = _now()
        with self._lock:
            data = self._load()
            if project_id not in data.get("projects", {}):
                raise KeyError("project not found")
            run_id = str(uuid4())
            run_record = {
                "id": run_id,
                "project_id": project_id,
                "status": RunStatus.PENDING.value,
                "progress": 0.0,
                "start_command": start_command,
                "created_at": _serialize_datetime(now),
                "updated_at": _serialize_datetime(now),
                "logs": [],
            }
            data.setdefault("runs", {})[run_id] = run_record
            data.setdefault("project_runs", {}).setdefault(project_id, []).append(run_id)
            self._save(data)
        return self._deserialize_run(run_record)

    def append_run_logs(self, run_id: str, logs: List[LogEntry]) -> RunDetail:
        if not logs:
            return self.get_run(run_id)
        with self._lock:
            data = self._load()
            run_record = data.get("runs", {}).get(run_id)
            if not run_record:
                raise KeyError("run not found")
            for entry in logs:
                run_record.setdefault("logs", []).append(
                    {
                        "timestamp": _serialize_datetime(entry.timestamp),
                        "level": entry.level,
                        "message": entry.message,
                    }
                )
            run_record["updated_at"] = _serialize_datetime(_now())
            self._save(data)
        return self._deserialize_run(run_record)

    def update_run_status(
        self, run_id: str, status: RunStatus, progress: Optional[float] = None
    ) -> RunDetail:
        with self._lock:
            data = self._load()
            run_record = data.get("runs", {}).get(run_id)
            if not run_record:
                raise KeyError("run not found")
            run_record["status"] = status.value
            if progress is not None:
                run_record["progress"] = progress
            run_record["updated_at"] = _serialize_datetime(_now())
            self._save(data)
        return self._deserialize_run(run_record)

    def get_run(self, run_id: str) -> RunDetail:
        data = self._load()
        record = data.get("runs", {}).get(run_id)
        if not record:
            raise KeyError("run not found")
        return self._deserialize_run(record)


__all__ = ["DatabaseStorage"]
