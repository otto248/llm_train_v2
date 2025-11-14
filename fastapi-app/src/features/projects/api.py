"""Project and run management endpoints."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path

from app import config
from app.deps import get_storage
from src.models import LogEntry, Project, ProjectCreate, ProjectDetail, RunDetail, RunStatus
from src.storage import DatabaseStorage
from src.utils import launch_training_process
from src.utils.filesystem import resolve_under_base

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)


def _build_start_command(project: ProjectDetail) -> str:
    return f"bash run_train_full_sft.sh {project.training_yaml_name}"


def _ensure_project_assets_available(project: ProjectDetail) -> None:
    missing: List[str] = []
    dataset_path = resolve_under_base(config.HOST_TRAINING_PATH, project.dataset_name)
    if not dataset_path.exists():
        missing.append(f"数据集 {project.dataset_name}")
    yaml_path = resolve_under_base(config.HOST_TRAINING_PATH, project.training_yaml_name)
    if not yaml_path.exists():
        missing.append(f"训练配置 {project.training_yaml_name}")
    if missing:
        raise HTTPException(status_code=400, detail="以下项目资源尚未上传完成：" + "、".join(missing))


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(payload: ProjectCreate, store: DatabaseStorage = Depends(get_storage)) -> ProjectDetail:
    """Persist a project record via :class:`DatabaseStorage`."""

    return store.create_project(payload)


@router.get("", response_model=List[Project])
def list_projects(store: DatabaseStorage = Depends(get_storage)) -> List[Project]:
    return list(store.list_projects())


@router.post("/{project_reference}/runs", response_model=RunDetail, status_code=201)
def create_run(
    project_reference: str = Path(..., description="Project identifier or unique name"),
    store: DatabaseStorage = Depends(get_storage),
) -> RunDetail:
    """Create a run row in the ``runs`` table before launching the training process."""

    project = store.get_project(project_reference)
    if project is None:
        project = store.get_project_by_name(project_reference)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    _ensure_project_assets_available(project)
    start_command = _build_start_command(project)
    run = store.create_run(project.id, start_command)
    run = store.append_run_logs(
        run.id,
        [
            LogEntry(
                timestamp=datetime.utcnow(),
                level="INFO",
                message=(
                    "已确认训练资源数据集 "
                    f"{project.dataset_name}，配置 {project.training_yaml_name}"
                ),
            )
        ],
    )
    try:
        process = launch_training_process(
            start_command,
            host_training_dir=config.HOST_TRAINING_DIR,
            docker_container_name=config.DOCKER_CONTAINER_NAME,
            docker_working_dir=config.DOCKER_WORKING_DIR,
            log=logger,
        )
    except RuntimeError as exc:
        store.append_run_logs(
            run.id,
            [
                LogEntry(
                    timestamp=datetime.utcnow(),
                    level="ERROR",
                    message=str(exc),
                )
            ],
        )
        store.update_run_status(run.id, RunStatus.FAILED)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    store.append_run_logs(
        run.id,
        [
            LogEntry(
                timestamp=datetime.utcnow(),
                level="INFO",
                message=f"已触发训练命令：{start_command} (PID {process.pid})",
            )
        ],
    )
    run = store.update_run_status(run.id, RunStatus.RUNNING, progress=0.05)
    return run


__all__ = ["router"]
