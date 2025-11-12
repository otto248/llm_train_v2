"""Dataset training configuration upload endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import config
from app.deps import get_storage
from src.storage import DatabaseStorage
from src.utils.filesystem import ensure_directories

router = APIRouter(prefix="/v1/datasets", tags=["train-configs"])


@router.put("/{dataset_id}/train-config", response_model=Dict[str, Any])
async def upload_train_config(
    dataset_id: str,
    file: UploadFile = File(...),
    store: DatabaseStorage = Depends(get_storage),
) -> Dict[str, Any]:
    record = store.get_dataset(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if not (file.filename.endswith(".yaml") or file.filename.endswith(".yml")):
        raise HTTPException(status_code=400, detail="Only YAML files are allowed")

    content = await file.read()
    if len(content) > config.MAX_YAML_BYTES:
        raise HTTPException(status_code=413, detail="YAML file too large (max 5MB)")

    ensure_directories(config.TRAIN_CONFIG_DIR)
    config_path = config.TRAIN_CONFIG_DIR / f"{dataset_id}_train.yaml"
    config_path.write_bytes(content)

    uploaded_at = datetime.now(timezone.utc)
    store.set_train_config(dataset_id, file.filename, uploaded_at, len(content))
    train_config = {
        "filename": file.filename,
        "uploaded_at": uploaded_at.isoformat().replace("+00:00", "Z"),
        "size": len(content),
    }
    return {"dataset_id": dataset_id, "train_config": train_config}


@router.get("/{dataset_id}/train-config", response_model=Dict[str, Any])
def get_train_config(
    dataset_id: str, store: DatabaseStorage = Depends(get_storage)
) -> Dict[str, Any]:
    record = store.get_dataset(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if not record.train_config:
        raise HTTPException(status_code=404, detail="Train config not uploaded yet")
    return {
        "filename": record.train_config.filename,
        "uploaded_at": record.train_config.uploaded_at.isoformat().replace("+00:00", "Z"),
        "size": record.train_config.size,
    }


@router.delete("/{dataset_id}/train-config", response_model=Dict[str, Any])
def delete_train_config(
    dataset_id: str, store: DatabaseStorage = Depends(get_storage)
) -> Dict[str, Any]:
    record = store.get_dataset(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    config_path = config.TRAIN_CONFIG_DIR / f"{dataset_id}_train.yaml"
    if config_path.exists():
        config_path.unlink()
    store.clear_train_config(dataset_id)
    return {"dataset_id": dataset_id, "status": "train_config_deleted"}


__all__ = ["router"]
