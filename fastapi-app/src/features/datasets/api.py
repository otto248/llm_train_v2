"""Dataset management and upload endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import config
from app.deps import get_storage
from src.models.datasets import DatasetCreateRequest, DatasetResponse
from src.storage import DatabaseStorage
from src.utils.filesystem import ensure_directories

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


@router.post("", response_model=Dict[str, Any], status_code=201)
def create_dataset(
    payload: DatasetCreateRequest, store: DatabaseStorage = Depends(get_storage)
) -> Dict[str, Any]:
    """Create a new dataset metadata entry."""

    record = store.create_dataset(payload)
    created_at = record.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {"id": record.id, "created_at": created_at}


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, store: DatabaseStorage = Depends(get_storage)) -> DatasetResponse:
    record = store.get_dataset(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    upload_progress = {"files_count": len(record.files)}
    return DatasetResponse(
        **record.model_dump(by_alias=True), upload_progress=upload_progress
    )


@router.put("/{dataset_id}/files", response_model=Dict[str, Any])
async def upload_small_file(
    dataset_id: str,
    file: UploadFile = File(...),
    store: DatabaseStorage = Depends(get_storage),
) -> Dict[str, Any]:
    record = store.get_dataset(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    content = await file.read()
    size = len(content)
    if size > config.MAX_SMALL_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large for direct upload")

    ensure_directories(config.FILES_DIR)
    upload_id = str(uuid.uuid4())
    stored_filename = f"{upload_id}_{file.filename}"
    stored_path = config.FILES_DIR / stored_filename
    stored_path.write_bytes(content)

    uploaded_at = datetime.now(timezone.utc)
    store.add_dataset_file(
        dataset_id,
        upload_id,
        file.filename,
        stored_filename,
        size,
        uploaded_at,
    )
    return {
        "upload_id": upload_id,
        "dataset_id": dataset_id,
        "bytes": size,
        "filename": file.filename,
    }


@router.delete("/uploads/{upload_id}")
def abort_upload(upload_id: str, store: DatabaseStorage = Depends(get_storage)) -> Dict[str, Any]:
    """Abort an upload session and clean associated files."""

    upload_info = store.remove_upload(upload_id)
    if upload_info is None:
        raise HTTPException(status_code=404, detail="Upload session not found")

    stored_filename = upload_info.get("stored_filename")
    if stored_filename:
        stored_path = config.FILES_DIR / stored_filename
        if stored_path.exists():
            stored_path.unlink()
    return {"upload_id": upload_id, "status": "aborted"}


__all__ = ["router"]
