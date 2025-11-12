"""Dataset management and upload endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import config
from src.utils.filesystem import ensure_directories

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


class DatasetCreateRequest(BaseModel):
    name: str
    dtype: Optional[str] = Field(
        default=None,
        alias="type",
        validation_alias="type",
        serialization_alias="type",
    )
    source: Optional[str] = None
    task_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class DatasetRecord(BaseModel):
    id: str
    name: str
    type: Optional[str] = Field(
        default=None,
        alias="type",
        validation_alias="type",
        serialization_alias="type",
    )
    source: Optional[str] = None
    task_type: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    status: str
    files: List[Dict[str, Any]] = Field(default_factory=list)
    train_config: Optional[Dict[str, Any]] = None


class DatasetResponse(DatasetRecord):
    upload_progress: Dict[str, Any]


def _dataset_path(dataset_id: str) -> Path:
    return config.DATASET_DIR / f"{dataset_id}.json"


def save_dataset_record(record: DatasetRecord) -> None:
    ensure_directories(config.DATASET_DIR)
    path = _dataset_path(record.id)
    path.write_text(
        record.model_dump_json(by_alias=True, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_dataset_record(dataset_id: str) -> DatasetRecord:
    path = _dataset_path(dataset_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Dataset not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    return DatasetRecord(**data)


@router.post("", response_model=Dict[str, Any], status_code=201)
def create_dataset(payload: DatasetCreateRequest) -> Dict[str, Any]:
    """Create a new dataset metadata entry."""

    ensure_directories(config.DATASET_DIR)
    dataset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=timezone.utc)
    record = DatasetRecord(
        id=dataset_id,
        name=payload.name,
        type=payload.dtype,
        source=payload.source,
        task_type=payload.task_type,
        metadata=payload.metadata or {},
        created_at=now.isoformat().replace("+00:00", "Z"),
        status="created",
    )
    save_dataset_record(record)
    return {"id": dataset_id, "created_at": record.created_at}


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str) -> DatasetResponse:
    record = load_dataset_record(dataset_id)
    upload_progress = {"files_count": len(record.files)}
    return DatasetResponse(
        **record.model_dump(by_alias=True), upload_progress=upload_progress
    )


@router.put("/{dataset_id}/files", response_model=Dict[str, Any])
async def upload_small_file(dataset_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    record = load_dataset_record(dataset_id)
    content = await file.read()
    size = len(content)
    if size > config.MAX_SMALL_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large for direct upload")

    ensure_directories(config.FILES_DIR, config.UPLOADS_DIR)
    upload_id = str(uuid.uuid4())
    stored_filename = f"{upload_id}_{file.filename}"
    stored_path = config.FILES_DIR / stored_filename
    stored_path.write_bytes(content)

    upload_record = {
        "upload_id": upload_id,
        "dataset_id": dataset_id,
        "filename": file.filename,
        "stored_filename": stored_filename,
        "bytes": size,
        "created_at": datetime.now(timezone.utc).replace(tzinfo=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "status": "completed",
    }
    (config.UPLOADS_DIR / f"{upload_id}.json").write_text(
        json.dumps(upload_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    file_entry = {
        "upload_id": upload_id,
        "name": file.filename,
        "stored_name": stored_filename,
        "bytes": size,
        "uploaded_at": upload_record["created_at"],
    }
    record.files.append(file_entry)
    record.status = "ready"
    save_dataset_record(record)
    return {
        "upload_id": upload_id,
        "dataset_id": dataset_id,
        "bytes": size,
        "filename": file.filename,
    }


@router.delete("/uploads/{upload_id}")
def abort_upload(upload_id: str) -> Dict[str, Any]:
    """Abort an upload session and clean associated files."""

    meta_path = config.UPLOADS_DIR / f"{upload_id}.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Upload session not found")
    upload_record = json.loads(meta_path.read_text(encoding="utf-8"))
    stored_filename = upload_record.get("stored_filename")
    if stored_filename:
        stored_path = config.FILES_DIR / stored_filename
        if stored_path.exists():
            stored_path.unlink()
    meta_path.unlink()

    dataset_id = upload_record.get("dataset_id")
    if dataset_id:
        try:
            record = load_dataset_record(dataset_id)
        except HTTPException:
            record = None
        if record:
            before = len(record.files)
            record.files = [f for f in record.files if f.get("upload_id") != upload_id]
            if len(record.files) != before:
                save_dataset_record(record)
    return {"upload_id": upload_id, "status": "aborted"}


__all__ = [
    "router",
    "DatasetRecord",
    "load_dataset_record",
    "save_dataset_record",
]
