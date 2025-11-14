"""SQLAlchemy-backed storage for service metadata."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

from src.models import LogEntry, Project, ProjectCreate, ProjectDetail, RunDetail, RunStatus
from src.models.datasets import (
    DatasetCreateRequest,
    DatasetFileEntry,
    DatasetMetadata,
    DatasetRecord,
    DatasetTrainConfig,
)
from src.utils.filesystem import ensure_directories


Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    """Return a timezone-aware datetime, assuming UTC for naive values."""

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_json(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    return json.loads(text)


UUID_LENGTH = 36
DEFAULT_STRING_LENGTH = 255


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(String(UUID_LENGTH), primary_key=True)
    name = Column(String(DEFAULT_STRING_LENGTH), nullable=False)
    dtype = Column(String(DEFAULT_STRING_LENGTH), nullable=True)
    source = Column(String(DEFAULT_STRING_LENGTH), nullable=True)
    task_type = Column(String(DEFAULT_STRING_LENGTH), nullable=True)
    metadata_json = Column(Text, nullable=True)
    status = Column(String(50), default="created", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    files = relationship("DatasetFile", cascade="all, delete-orphan", back_populates="dataset")
    train_config = relationship(
        "TrainConfig", cascade="all, delete-orphan", back_populates="dataset", uselist=False
    )


class DatasetFile(Base):
    __tablename__ = "dataset_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(
        String(UUID_LENGTH), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    upload_id = Column(String(DEFAULT_STRING_LENGTH), unique=True, nullable=False)
    name = Column(String(DEFAULT_STRING_LENGTH), nullable=False)
    stored_name = Column(String(DEFAULT_STRING_LENGTH), nullable=False)
    bytes = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    dataset = relationship("Dataset", back_populates="files")


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    upload_id = Column(String(DEFAULT_STRING_LENGTH), primary_key=True)
    dataset_id = Column(
        String(UUID_LENGTH), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    filename = Column(String(DEFAULT_STRING_LENGTH), nullable=False)
    stored_filename = Column(String(DEFAULT_STRING_LENGTH), nullable=False)
    bytes = Column(Integer, nullable=False)
    status = Column(String(50), default="completed", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class TrainConfig(Base):
    __tablename__ = "train_configs"

    dataset_id = Column(
        String(UUID_LENGTH), ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    filename = Column(String(DEFAULT_STRING_LENGTH), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    size = Column(Integer, nullable=False)

    dataset = relationship("Dataset", back_populates="train_config")


class ProjectModel(Base):
    __tablename__ = "projects"

    id = Column(String(UUID_LENGTH), primary_key=True)
    name = Column(String(DEFAULT_STRING_LENGTH), unique=True, nullable=False)
    dataset_name = Column(String(DEFAULT_STRING_LENGTH), nullable=False)
    training_yaml_name = Column(String(DEFAULT_STRING_LENGTH), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    runs = relationship("RunModel", cascade="all, delete-orphan", back_populates="project")


class RunModel(Base):
    __tablename__ = "runs"

    id = Column(String(UUID_LENGTH), primary_key=True)
    project_id = Column(
        String(UUID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String(50), default=RunStatus.PENDING.value, nullable=False)
    progress = Column(Float, default=0.0, nullable=False)
    start_command = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    project = relationship("ProjectModel", back_populates="runs")
    logs = relationship("RunLogModel", cascade="all, delete-orphan", back_populates="run")


class RunLogModel(Base):
    __tablename__ = "run_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(UUID_LENGTH), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    level = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)

    run = relationship("RunModel", back_populates="logs")


class DeploymentModel(Base):
    __tablename__ = "deployments"

    deployment_id = Column(String(UUID_LENGTH), primary_key=True)
    model_path = Column(Text, nullable=False)
    model_version = Column(String(100), nullable=True)
    tags_json = Column(Text, nullable=True)
    gpu_id = Column(Integer, nullable=True)
    port = Column(Integer, nullable=False)
    pid = Column(Integer, nullable=True)
    status = Column(String(50), nullable=False, default="starting")
    started_at = Column(Float, nullable=True)
    stopped_at = Column(Float, nullable=True)
    health_ok = Column(Boolean, nullable=True)
    vllm_cmd = Column(Text, nullable=True)
    log_file = Column(Text, nullable=True)
    health_path = Column(String(DEFAULT_STRING_LENGTH), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class DatabaseStorage:
    """High-level storage abstraction backed by SQLAlchemy."""

    def __init__(self, database_url: str, database_path: Path):
        ensure_directories(database_path.parent)
        self._engine = create_engine(database_url, future=True)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False, future=True)

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # Dataset operations -------------------------------------------------
    def create_dataset(self, payload: DatasetCreateRequest) -> DatasetRecord:
        metadata = payload.metadata or DatasetMetadata()
        dataset = Dataset(
            id=str(uuid4()),
            name=payload.name,
            dtype=payload.dtype,
            source=payload.source,
            task_type=payload.task_type,
            metadata_json=_as_json(metadata.model_dump(exclude_none=True)),
            status="created",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        with self._session() as session:
            session.add(dataset)
            session.flush()
            record = self._to_dataset_record(dataset)
        return record

    def get_dataset(self, dataset_id: str) -> Optional[DatasetRecord]:
        with self._session() as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                return None
            record = self._to_dataset_record(dataset)
        return record

    def add_dataset_file(
        self,
        dataset_id: str,
        upload_id: str,
        filename: str,
        stored_filename: str,
        size: int,
        uploaded_at: datetime,
    ) -> DatasetRecord:
        with self._session() as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise KeyError("dataset not found")
            file_entry = DatasetFile(
                dataset_id=dataset_id,
                upload_id=upload_id,
                name=filename,
                stored_name=stored_filename,
                bytes=size,
                uploaded_at=uploaded_at,
            )
            session.add(file_entry)
            session.add(
                UploadSession(
                    upload_id=upload_id,
                    dataset_id=dataset_id,
                    filename=filename,
                    stored_filename=stored_filename,
                    bytes=size,
                    status="completed",
                    created_at=uploaded_at,
                )
            )
            dataset.status = "ready"
            dataset.updated_at = _utcnow()
            session.flush()
            self._recalculate_dataset_file_metadata(session, dataset)
            session.flush()
            session.refresh(dataset)
            record = self._to_dataset_record(dataset)
        return record

    def remove_upload(self, upload_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            upload = session.get(UploadSession, upload_id)
            if upload is None:
                return None
            dataset = session.get(Dataset, upload.dataset_id)
            if dataset is None:
                return None
            file = session.execute(
                select(DatasetFile).where(DatasetFile.upload_id == upload_id)
            ).scalar_one_or_none()
            if file:
                session.delete(file)
            session.delete(upload)
            metadata = self._recalculate_dataset_file_metadata(session, dataset)
            dataset.updated_at = _utcnow()
            if metadata.total_files:
                dataset.status = "ready"
            else:
                dataset.status = "created"
            session.flush()
            session.refresh(dataset)
            session.expunge(dataset)
            info = {
                "dataset_id": upload.dataset_id,
                "filename": upload.filename,
                "stored_filename": upload.stored_filename,
            }
        return info

    def set_train_config(
        self, dataset_id: str, filename: str, uploaded_at: datetime, size: int
    ) -> DatasetRecord:
        with self._session() as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise KeyError("dataset not found")
            config_row = session.get(TrainConfig, dataset_id)
            if config_row is None:
                config_row = TrainConfig(
                    dataset_id=dataset_id,
                    filename=filename,
                    uploaded_at=uploaded_at,
                    size=size,
                )
                session.add(config_row)
            else:
                config_row.filename = filename
                config_row.uploaded_at = uploaded_at
                config_row.size = size
            metadata = self._get_dataset_metadata(dataset)
            metadata.has_train_config = True
            self._set_dataset_metadata(dataset, metadata)
            dataset.status = "train_config_uploaded"
            dataset.updated_at = _utcnow()
            session.flush()
            session.refresh(dataset)
            record = self._to_dataset_record(dataset)
        return record

    def clear_train_config(self, dataset_id: str) -> DatasetRecord:
        with self._session() as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise KeyError("dataset not found")
            config_row = session.get(TrainConfig, dataset_id)
            if config_row:
                session.delete(config_row)
            metadata = self._get_dataset_metadata(dataset)
            metadata.has_train_config = False
            self._set_dataset_metadata(dataset, metadata)
            dataset.status = "train_config_deleted"
            dataset.updated_at = _utcnow()
            session.flush()
            session.refresh(dataset)
            record = self._to_dataset_record(dataset)
        return record

    # Internal helpers ---------------------------------------------------
    def _get_dataset_metadata(self, dataset: Dataset) -> DatasetMetadata:
        raw = _from_json(dataset.metadata_json, {})
        return DatasetMetadata.model_validate(raw)

    def _set_dataset_metadata(self, dataset: Dataset, metadata: DatasetMetadata) -> None:
        dataset.metadata_json = _as_json(metadata.model_dump(exclude_none=True))

    def _recalculate_dataset_file_metadata(
        self, session: Session, dataset: Dataset
    ) -> DatasetMetadata:
        total_files, total_bytes = session.execute(
            select(
                func.count(DatasetFile.id),
                func.coalesce(func.sum(DatasetFile.bytes), 0),
            ).where(DatasetFile.dataset_id == dataset.id)
        ).one()
        metadata = self._get_dataset_metadata(dataset)
        metadata.total_files = int(total_files or 0)
        metadata.total_bytes = int(total_bytes or 0)
        self._set_dataset_metadata(dataset, metadata)
        return metadata

    # Project operations -------------------------------------------------
    def create_project(self, payload: ProjectCreate) -> ProjectDetail:
        project = ProjectModel(
            id=str(uuid4()),
            name=payload.name,
            dataset_name=payload.dataset_name,
            training_yaml_name=payload.training_yaml_name,
            description=payload.description,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        with self._session() as session:
            session.add(project)
            session.flush()
            session.refresh(project)
            session.expunge(project)
        return self._to_project_detail(project, runs=[])

    def list_projects(self) -> Iterable[Project]:
        with self._session() as session:
            records = session.execute(select(ProjectModel)).scalars().all()
            for record in records:
                session.expunge(record)
        return [self._to_project_summary(project) for project in records]

    def get_project(self, project_id: str) -> Optional[ProjectDetail]:
        with self._session() as session:
            project = session.get(ProjectModel, project_id)
            if project is None:
                return None
            runs = list(project.runs)
            for run in runs:
                session.expunge(run)
                for log in run.logs:
                    session.expunge(log)
            session.expunge(project)
        return self._to_project_detail(project, runs=runs)

    def get_project_by_name(self, name: str) -> Optional[ProjectDetail]:
        with self._session() as session:
            project = session.execute(
                select(ProjectModel).where(ProjectModel.name == name)
            ).scalar_one_or_none()
            if project is None:
                return None
            runs = list(project.runs)
            for run in runs:
                session.expunge(run)
                for log in run.logs:
                    session.expunge(log)
            session.expunge(project)
        return self._to_project_detail(project, runs=runs)

    def create_run(self, project_id: str, start_command: str) -> RunDetail:
        run = RunModel(
            id=str(uuid4()),
            project_id=project_id,
            status=RunStatus.PENDING.value,
            progress=0.0,
            start_command=start_command,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        with self._session() as session:
            project = session.get(ProjectModel, project_id)
            if project is None:
                raise KeyError("project not found")
            session.add(run)
            session.flush()
            session.refresh(run)
            session.expunge(run)
        return self._to_run_detail(run)

    def get_run(self, run_id: str) -> Optional[RunDetail]:
        with self._session() as session:
            run = session.get(RunModel, run_id)
            if run is None:
                return None
            logs = list(run.logs)
            for log in logs:
                session.expunge(log)
            session.expunge(run)
        return self._to_run_detail(run)

    def append_run_logs(self, run_id: str, logs: List[LogEntry]) -> RunDetail:
        if not logs:
            run = self.get_run(run_id)
            if run is None:
                raise KeyError("run not found")
            return run
        with self._session() as session:
            run = session.get(RunModel, run_id)
            if run is None:
                raise KeyError("run not found")
            for entry in logs:
                session.add(
                    RunLogModel(
                        run_id=run_id,
                        timestamp=entry.timestamp.astimezone(timezone.utc),
                        level=entry.level,
                        message=entry.message,
                    )
                )
            run.updated_at = _utcnow()
            session.flush()
            session.refresh(run)
            session.expunge(run)
            for log in run.logs:
                session.expunge(log)
        return self._to_run_detail(run)

    def update_run_status(
        self, run_id: str, status: RunStatus, progress: Optional[float] = None
    ) -> RunDetail:
        with self._session() as session:
            run = session.get(RunModel, run_id)
            if run is None:
                raise KeyError("run not found")
            run.status = status.value
            if progress is not None:
                run.progress = progress
            run.updated_at = _utcnow()
            session.flush()
            session.refresh(run)
            session.expunge(run)
            for log in run.logs:
                session.expunge(log)
        return self._to_run_detail(run)

    # Deployment operations ----------------------------------------------
    def create_deployment_record(self, info: Dict[str, Any]) -> Dict[str, Any]:
        record = DeploymentModel(
            deployment_id=info["deployment_id"],
            model_path=info["model_path"],
            model_version=info.get("model_version"),
            tags_json=_as_json(info.get("tags", [])),
            gpu_id=info.get("gpu_id"),
            port=info["port"],
            pid=info.get("pid"),
            status=info.get("status", "starting"),
            started_at=info.get("started_at"),
            stopped_at=info.get("stopped_at"),
            health_ok=info.get("health_ok"),
            vllm_cmd=info.get("vllm_cmd"),
            log_file=info.get("log_file"),
            health_path=info.get("health_path"),
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        with self._session() as session:
            session.add(record)
            session.flush()
            session.refresh(record)
            session.expunge(record)
        return self._to_deployment_dict(record)

    def update_deployment(self, deployment_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            record = session.get(DeploymentModel, deployment_id)
            if record is None:
                return None
            for key, value in fields.items():
                if key == "tags":
                    setattr(record, "tags_json", _as_json(value))
                else:
                    setattr(record, key, value)
            record.updated_at = _utcnow()
            session.flush()
            session.refresh(record)
            session.expunge(record)
        return self._to_deployment_dict(record)

    def get_deployment(self, deployment_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            record = session.get(DeploymentModel, deployment_id)
            if record is None:
                return None
            session.expunge(record)
        return self._to_deployment_dict(record)

    def delete_deployment(self, deployment_id: str) -> None:
        with self._session() as session:
            record = session.get(DeploymentModel, deployment_id)
            if record:
                session.delete(record)

    def list_deployments(
        self,
        *,
        model: Optional[str] = None,
        tag: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._session() as session:
            query = select(DeploymentModel)
            if status:
                query = query.where(DeploymentModel.status == status)
            records = session.execute(query).scalars().all()
            result: List[Dict[str, Any]] = []
            for record in records:
                payload = self._to_deployment_dict(record)
                if model and model not in payload.get("model_path", ""):
                    continue
                if tag and tag not in payload.get("tags", []):
                    continue
                result.append(payload)
        return result

    # Conversion helpers -------------------------------------------------
    def _to_dataset_record(self, dataset: Dataset) -> DatasetRecord:
        files = [
            DatasetFileEntry(
                upload_id=file.upload_id,
                name=file.name,
                stored_name=file.stored_name,
                bytes=file.bytes,
                uploaded_at=_ensure_aware(file.uploaded_at),
            )
            for file in sorted(
                dataset.files, key=lambda item: _ensure_aware(item.uploaded_at)
            )
        ]
        train_config = None
        if dataset.train_config:
            train_config = DatasetTrainConfig(
                filename=dataset.train_config.filename,
                uploaded_at=_ensure_aware(dataset.train_config.uploaded_at),
                size=dataset.train_config.size,
            )
        metadata = DatasetMetadata.model_validate(_from_json(dataset.metadata_json, {}))
        return DatasetRecord(
            id=dataset.id,
            name=dataset.name,
            dtype=dataset.dtype,
            source=dataset.source,
            task_type=dataset.task_type,
            metadata=metadata,
            created_at=dataset.created_at,
            status=dataset.status,
            files=files,
            train_config=train_config,
        )

    def _to_project_summary(self, project: ProjectModel) -> Project:
        return Project(
            id=project.id,
            name=project.name,
            dataset_name=project.dataset_name,
            training_yaml_name=project.training_yaml_name,
            description=project.description,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    def _to_project_detail(
        self, project: ProjectModel, runs: Iterable[RunModel]
    ) -> ProjectDetail:
        return ProjectDetail(
            id=project.id,
            name=project.name,
            dataset_name=project.dataset_name,
            training_yaml_name=project.training_yaml_name,
            description=project.description,
            created_at=project.created_at,
            updated_at=project.updated_at,
            runs=[self._to_run_detail(run) for run in runs],
        )

    def _to_run_detail(self, run: RunModel) -> RunDetail:
        log_entries = [
            LogEntry(timestamp=log.timestamp, level=log.level, message=log.message)
            for log in sorted(run.logs, key=lambda item: item.timestamp)
        ]
        return RunDetail(
            id=run.id,
            project_id=run.project_id,
            status=RunStatus(run.status),
            progress=run.progress,
            start_command=run.start_command,
            created_at=run.created_at,
            updated_at=run.updated_at,
            logs=log_entries,
        )

    def _to_deployment_dict(self, record: DeploymentModel) -> Dict[str, Any]:
        return {
            "deployment_id": record.deployment_id,
            "model_path": record.model_path,
            "model_version": record.model_version,
            "tags": _from_json(record.tags_json, []),
            "gpu_id": record.gpu_id,
            "port": record.port,
            "pid": record.pid,
            "status": record.status,
            "started_at": record.started_at,
            "stopped_at": record.stopped_at,
            "health_ok": record.health_ok,
            "vllm_cmd": record.vllm_cmd,
            "log_file": record.log_file,
            "health_path": record.health_path,
        }


__all__ = ["DatabaseStorage"]
