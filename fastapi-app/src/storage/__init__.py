"""File-based metadata storage for the LLM platform."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from src.models import LogEntry, Project, ProjectCreate, ProjectDetail, RunDetail, RunStatus
from src.models.datasets import (
    DatasetCreateRequest,
    DatasetFileEntry,
    DatasetRecord,
    DatasetTrainConfig,
)
from src.utils.filesystem import ensure_directories


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


class FileStorage:
    """Persist service metadata inside a single JSON document."""

    def __init__(self, metadata_path: Path):
        self._metadata_path = metadata_path
        ensure_directories(metadata_path.parent)
        self._lock = Lock()
        if not metadata_path.exists():
            self._write_state(self._empty_state())

    # ------------------------------------------------------------------
    # State helpers
    def _empty_state(self) -> Dict[str, Any]:
        return {
            "datasets": {},
            "uploads": {},
            "projects": {},
            "runs": {},
            "deployments": {},
        }

    def _read_state(self) -> Dict[str, Any]:
        try:
            with self._metadata_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            state = self._empty_state()
            self._write_state(state)
            return state

    def _write_state(self, state: Dict[str, Any]) -> None:
        temp_path = self._metadata_path.with_suffix(self._metadata_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        temp_path.replace(self._metadata_path)

    # ------------------------------------------------------------------
    # Dataset operations
    def create_dataset(self, payload: DatasetCreateRequest) -> DatasetRecord:
        dataset_id = str(uuid4())
        now = _utcnow()
        dataset_payload = {
            "id": dataset_id,
            "name": payload.name,
            "type": payload.dtype,
            "source": payload.source,
            "task_type": payload.task_type,
            "metadata": payload.metadata or {},
            "status": "created",
            "created_at": _to_iso(now),
            "updated_at": _to_iso(now),
            "files": [],
            "train_config": None,
        }
        with self._lock:
            state = self._read_state()
            state["datasets"][dataset_id] = dataset_payload
            self._write_state(state)
        return self._dataset_record_from_dict(dataset_payload)

    def get_dataset(self, dataset_id: str) -> Optional[DatasetRecord]:
        with self._lock:
            state = self._read_state()
            dataset = state["datasets"].get(dataset_id)
            dataset_copy = deepcopy(dataset) if dataset else None
        if dataset_copy is None:
            return None
        return self._dataset_record_from_dict(dataset_copy)

    def add_dataset_file(
        self,
        dataset_id: str,
        upload_id: str,
        filename: str,
        stored_filename: str,
        size: int,
        uploaded_at: datetime,
    ) -> DatasetRecord:
        with self._lock:
            state = self._read_state()
            dataset = state["datasets"].get(dataset_id)
            if dataset is None:
                raise KeyError("dataset not found")
            file_payload = {
                "upload_id": upload_id,
                "name": filename,
                "stored_name": stored_filename,
                "bytes": size,
                "uploaded_at": _to_iso(uploaded_at),
            }
            dataset.setdefault("files", []).append(file_payload)
            dataset["status"] = "ready"
            dataset["updated_at"] = _to_iso(_utcnow())
            state["uploads"][upload_id] = {
                "dataset_id": dataset_id,
                "filename": filename,
                "stored_filename": stored_filename,
            }
            updated = deepcopy(dataset)
            self._write_state(state)
        return self._dataset_record_from_dict(updated)

    def remove_upload(self, upload_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            upload = state["uploads"].get(upload_id)
            if upload is None:
                return None
            dataset = state["datasets"].get(upload["dataset_id"])
            if dataset is None:
                return None
            files = dataset.get("files", [])
            dataset["files"] = [
                item for item in files if item.get("upload_id") != upload_id
            ]
            dataset["updated_at"] = _to_iso(_utcnow())
            dataset["status"] = "ready" if dataset["files"] else "created"
            state["uploads"].pop(upload_id, None)
            info = deepcopy(upload)
            self._write_state(state)
        return info

    def set_train_config(
        self, dataset_id: str, filename: str, uploaded_at: datetime, size: int
    ) -> DatasetRecord:
        with self._lock:
            state = self._read_state()
            dataset = state["datasets"].get(dataset_id)
            if dataset is None:
                raise KeyError("dataset not found")
            dataset["train_config"] = {
                "filename": filename,
                "uploaded_at": _to_iso(uploaded_at),
                "size": size,
            }
            dataset["status"] = "train_config_uploaded"
            dataset["updated_at"] = _to_iso(_utcnow())
            updated = deepcopy(dataset)
            self._write_state(state)
        return self._dataset_record_from_dict(updated)

    def clear_train_config(self, dataset_id: str) -> DatasetRecord:
        with self._lock:
            state = self._read_state()
            dataset = state["datasets"].get(dataset_id)
            if dataset is None:
                raise KeyError("dataset not found")
            dataset["train_config"] = None
            dataset["status"] = "train_config_deleted"
            dataset["updated_at"] = _to_iso(_utcnow())
            updated = deepcopy(dataset)
            self._write_state(state)
        return self._dataset_record_from_dict(updated)

    # ------------------------------------------------------------------
    # Project operations
    def create_project(self, payload: ProjectCreate) -> ProjectDetail:
        project_id = str(uuid4())
        now = _utcnow()
        project_payload = {
            "id": project_id,
            "name": payload.name,
            "dataset_name": payload.dataset_name,
            "training_yaml_name": payload.training_yaml_name,
            "description": payload.description,
            "created_at": _to_iso(now),
            "updated_at": _to_iso(now),
        }
        with self._lock:
            state = self._read_state()
            state["projects"][project_id] = project_payload
            self._write_state(state)
        return self._project_detail_from_dict(project_payload, runs=[])

    def list_projects(self) -> Iterable[Project]:
        with self._lock:
            state = self._read_state()
            projects = list(state["projects"].values())
        return [self._project_summary_from_dict(project) for project in projects]

    def get_project(self, project_id: str) -> Optional[ProjectDetail]:
        with self._lock:
            state = self._read_state()
            project = state["projects"].get(project_id)
            if project is None:
                return None
            project_copy = deepcopy(project)
            run_dicts = [
                deepcopy(run)
                for run in state["runs"].values()
                if run.get("project_id") == project_id
            ]
        run_dicts.sort(key=lambda item: item.get("created_at", ""))
        run_models = [self._run_detail_from_dict(run) for run in run_dicts]
        return self._project_detail_from_dict(project_copy, runs=run_models)

    def get_project_by_name(self, name: str) -> Optional[ProjectDetail]:
        with self._lock:
            state = self._read_state()
            project_match: Optional[Dict[str, Any]] = None
            run_dicts: List[Dict[str, Any]] = []
            for project in state["projects"].values():
                if project.get("name") == name:
                    project_match = deepcopy(project)
                    run_dicts = [
                        deepcopy(run)
                        for run in state["runs"].values()
                        if run.get("project_id") == project["id"]
                    ]
                    break
            if project_match is None:
                return None
        run_dicts.sort(key=lambda item: item.get("created_at", ""))
        run_models = [self._run_detail_from_dict(run) for run in run_dicts]
        return self._project_detail_from_dict(project_match, runs=run_models)

    def create_run(self, project_id: str, start_command: str) -> RunDetail:
        run_id = str(uuid4())
        now = _utcnow()
        run_payload = {
            "id": run_id,
            "project_id": project_id,
            "status": RunStatus.PENDING.value,
            "progress": 0.0,
            "start_command": start_command,
            "created_at": _to_iso(now),
            "updated_at": _to_iso(now),
            "logs": [],
        }
        with self._lock:
            state = self._read_state()
            if project_id not in state["projects"]:
                raise KeyError("project not found")
            state["runs"][run_id] = run_payload
            self._write_state(state)
        return self._run_detail_from_dict(run_payload)

    def get_run(self, run_id: str) -> Optional[RunDetail]:
        with self._lock:
            state = self._read_state()
            run = state["runs"].get(run_id)
            run_copy = deepcopy(run) if run else None
        if run_copy is None:
            return None
        return self._run_detail_from_dict(run_copy)

    def append_run_logs(self, run_id: str, logs: List[LogEntry]) -> RunDetail:
        with self._lock:
            state = self._read_state()
            run = state["runs"].get(run_id)
            if run is None:
                raise KeyError("run not found")
            for entry in logs:
                run.setdefault("logs", []).append(
                    {
                        "timestamp": _to_iso(entry.timestamp),
                        "level": entry.level,
                        "message": entry.message,
                    }
                )
            if logs:
                run["updated_at"] = _to_iso(_utcnow())
            updated = deepcopy(run)
            self._write_state(state)
        return self._run_detail_from_dict(updated)

    def update_run_status(
        self, run_id: str, status: RunStatus, progress: Optional[float] = None
    ) -> RunDetail:
        with self._lock:
            state = self._read_state()
            run = state["runs"].get(run_id)
            if run is None:
                raise KeyError("run not found")
            run["status"] = status.value
            if progress is not None:
                run["progress"] = progress
            run["updated_at"] = _to_iso(_utcnow())
            updated = deepcopy(run)
            self._write_state(state)
        return self._run_detail_from_dict(updated)

    # ------------------------------------------------------------------
    # Deployment operations
    def create_deployment_record(self, info: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "deployment_id": info["deployment_id"],
            "model_path": info["model_path"],
            "model_version": info.get("model_version"),
            "tags": list(info.get("tags", [])),
            "gpu_id": info.get("gpu_id"),
            "port": info["port"],
            "pid": info.get("pid"),
            "status": info.get("status", "starting"),
            "started_at": info.get("started_at"),
            "stopped_at": info.get("stopped_at"),
            "health_ok": info.get("health_ok"),
            "vllm_cmd": info.get("vllm_cmd"),
            "log_file": info.get("log_file"),
            "health_path": info.get("health_path"),
        }
        with self._lock:
            state = self._read_state()
            state["deployments"][payload["deployment_id"]] = payload
            self._write_state(state)
        return deepcopy(payload)

    def update_deployment(self, deployment_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            record = state["deployments"].get(deployment_id)
            if record is None:
                return None
            for key, value in fields.items():
                if key == "tags":
                    record[key] = list(value)
                else:
                    record[key] = value
            updated = deepcopy(record)
            self._write_state(state)
        return updated

    def get_deployment(self, deployment_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            record = state["deployments"].get(deployment_id)
        if record is None:
            return None
        return deepcopy(record)

    def delete_deployment(self, deployment_id: str) -> None:
        with self._lock:
            state = self._read_state()
            if deployment_id in state["deployments"]:
                state["deployments"].pop(deployment_id)
                self._write_state(state)

    def list_deployments(
        self,
        *,
        model: Optional[str] = None,
        tag: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            records = list(state["deployments"].values())
        result: List[Dict[str, Any]] = []
        for record in records:
            if status and record.get("status") != status:
                continue
            if model and model not in (record.get("model_path") or ""):
                continue
            if tag and tag not in record.get("tags", []):
                continue
            result.append(deepcopy(record))
        return result

    # ------------------------------------------------------------------
    # Conversion helpers
    def _dataset_record_from_dict(self, data: Dict[str, Any]) -> DatasetRecord:
        files = [
            DatasetFileEntry(
                upload_id=item["upload_id"],
                name=item["name"],
                stored_name=item["stored_name"],
                bytes=item["bytes"],
                uploaded_at=_from_iso(item["uploaded_at"]),
            )
            for item in sorted(
                data.get("files", []), key=lambda entry: entry.get("uploaded_at", "")
            )
        ]
        train_config = data.get("train_config")
        train_model = None
        if train_config:
            train_model = DatasetTrainConfig(
                filename=train_config["filename"],
                uploaded_at=_from_iso(train_config["uploaded_at"]),
                size=train_config["size"],
            )
        return DatasetRecord(
            id=data["id"],
            name=data["name"],
            dtype=data.get("type"),
            source=data.get("source"),
            task_type=data.get("task_type"),
            metadata=data.get("metadata", {}),
            created_at=_from_iso(data["created_at"]),
            status=data.get("status", "created"),
            files=files,
            train_config=train_model,
        )

    def _project_summary_from_dict(self, data: Dict[str, Any]) -> Project:
        return Project(
            id=data["id"],
            name=data["name"],
            dataset_name=data["dataset_name"],
            training_yaml_name=data["training_yaml_name"],
            description=data.get("description"),
            created_at=_from_iso(data["created_at"]),
            updated_at=_from_iso(data["updated_at"]),
        )

    def _project_detail_from_dict(
        self, data: Dict[str, Any], runs: Iterable[RunDetail]
    ) -> ProjectDetail:
        return ProjectDetail(
            id=data["id"],
            name=data["name"],
            dataset_name=data["dataset_name"],
            training_yaml_name=data["training_yaml_name"],
            description=data.get("description"),
            created_at=_from_iso(data["created_at"]),
            updated_at=_from_iso(data["updated_at"]),
            runs=list(runs),
        )

    def _run_detail_from_dict(self, data: Dict[str, Any]) -> RunDetail:
        log_entries = [
            LogEntry(
                timestamp=_from_iso(item["timestamp"]),
                level=item["level"],
                message=item["message"],
            )
            for item in sorted(data.get("logs", []), key=lambda entry: entry.get("timestamp", ""))
        ]
        return RunDetail(
            id=data["id"],
            project_id=data["project_id"],
            status=RunStatus(data.get("status", RunStatus.PENDING.value)),
            progress=float(data.get("progress", 0.0)),
            start_command=data.get("start_command", ""),
            created_at=_from_iso(data["created_at"]),
            updated_at=_from_iso(data["updated_at"]),
            logs=log_entries,
        )


__all__ = ["FileStorage"]
