"""Pydantic models for dataset metadata persisted in the database."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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


class DatasetFileEntry(BaseModel):
    upload_id: str
    name: str
    stored_name: str
    bytes: int
    uploaded_at: datetime


class DatasetTrainConfig(BaseModel):
    filename: str
    uploaded_at: datetime
    size: int


class DatasetRecord(BaseModel):
    id: str
    name: str
    dtype: Optional[str] = Field(
        default=None,
        alias="type",
        validation_alias="type",
        serialization_alias="type",
    )
    source: Optional[str] = None
    task_type: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    status: str
    files: List[DatasetFileEntry] = Field(default_factory=list)
    train_config: Optional[DatasetTrainConfig] = None


class DatasetResponse(DatasetRecord):
    upload_progress: Dict[str, Any]


__all__ = [
    "DatasetCreateRequest",
    "DatasetFileEntry",
    "DatasetRecord",
    "DatasetResponse",
    "DatasetTrainConfig",
]
