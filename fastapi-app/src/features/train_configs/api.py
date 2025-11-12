"""Dataset training configuration upload endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, UploadFile

from app import config
from src.features.datasets.api import load_dataset_record, save_dataset_record
from src.utils.filesystem import ensure_directories

router = APIRouter(prefix="/v1/datasets", tags=["train-configs"])


@router.put("/{dataset_id}/train-config", response_model=Dict[str, Any])
async def upload_train_config(dataset_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    record = load_dataset_record(dataset_id)
    if not (file.filename.endswith(".yaml") or file.filename.endswith(".yml")):
        raise HTTPException(status_code=400, detail="Only YAML files are allowed")

    content = await file.read()
    if len(content) > config.MAX_YAML_BYTES:
        raise HTTPException(status_code=413, detail="YAML file too large (max 5MB)")

    ensure_directories(config.TRAIN_CONFIG_DIR)
    config_path = config.TRAIN_CONFIG_DIR / f"{dataset_id}_train.yaml"
    config_path.write_bytes(content)

    record.train_config = {
        "filename": file.filename,
        "uploaded_at": datetime.now(timezone.utc)
        .replace(tzinfo=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "size": len(content),
    }
    record.status = "train_config_uploaded"
    save_dataset_record(record)
    return {"dataset_id": dataset_id, "train_config": record.train_config}


@router.get("/{dataset_id}/train-config", response_model=Dict[str, Any])
def get_train_config(dataset_id: str) -> Dict[str, Any]:
    record = load_dataset_record(dataset_id)
    if not record.train_config:
        raise HTTPException(status_code=404, detail="Train config not uploaded yet")
    return record.train_config


@router.delete("/{dataset_id}/train-config", response_model=Dict[str, Any])
def delete_train_config(dataset_id: str) -> Dict[str, Any]:
    record = load_dataset_record(dataset_id)
    config_path = config.TRAIN_CONFIG_DIR / f"{dataset_id}_train.yaml"
    if config_path.exists():
        config_path.unlink()
    record.train_config = None
    record.status = "train_config_deleted"
    save_dataset_record(record)
    return {"dataset_id": dataset_id, "status": "train_config_deleted"}


__all__ = ["router"]
