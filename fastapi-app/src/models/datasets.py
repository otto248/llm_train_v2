"""Pydantic models for dataset metadata persisted in the database."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DatasetMetadata(BaseModel):
    """Structured metadata persisted alongside dataset records."""

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    version: Optional[str] = None
    records: Optional[int] = Field(default=None, ge=0)
    license: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    total_files: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    has_train_config: bool = False


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
    metadata: Optional[DatasetMetadata] = None


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
    metadata: DatasetMetadata = Field(default_factory=DatasetMetadata)
    created_at: datetime
    status: str
    files: List[DatasetFileEntry] = Field(default_factory=list)
    train_config: Optional[DatasetTrainConfig] = None


class DatasetResponse(DatasetRecord):
    upload_progress: Dict[str, Any]


__all__ = [
    "DatasetMetadata",
    "DatasetCreateRequest",
    "DatasetFileEntry",
    "DatasetRecord",
    "DatasetResponse",
    "DatasetTrainConfig",
]
