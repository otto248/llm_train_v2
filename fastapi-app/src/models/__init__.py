"""Shared Pydantic models for the platform."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    STOPPED = "stopped"


class LogEntry(BaseModel):
    timestamp: datetime
    level: str
    message: str


class ProjectCreate(BaseModel):
    name: str
    dataset_name: str
    training_yaml_name: str
    description: Optional[str] = None


class Project(BaseModel):
    id: str
    name: str
    dataset_name: str
    training_yaml_name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ProjectDetail(Project):
    runs: List["RunDetail"] = Field(default_factory=list)


class RunDetail(BaseModel):
    id: str
    project_id: str
    status: RunStatus
    progress: float = 0.0
    start_command: str
    created_at: datetime
    updated_at: datetime
    logs: List[LogEntry] = Field(default_factory=list)


ProjectDetail.model_rebuild()

__all__ = [
    "LogEntry",
    "Project",
    "ProjectCreate",
    "ProjectDetail",
    "RunDetail",
    "RunStatus",
]
