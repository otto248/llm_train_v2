"""Project and run related API endpoints."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path as PathlibPath
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam

from ..config import (
    DOCKER_CONTAINER_NAME,
    DOCKER_WORKING_DIR,
    HOST_TRAINING_DIR,
    HOST_TRAINING_PATH,
)
from ..dependencies import get_storage
from ..models import LogEntry, Project, ProjectCreate, ProjectDetail, RunDetail, RunStatus
from ..storage import FileStorage
from ..utils import launch_training_process

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)


def _build_start_command(project: ProjectDetail) -> str:
    """生成启动训练脚本所需的命令行。"""

    return f"bash run_train_full_sft.sh {project.training_yaml_name}"


def _resolve_project_asset(relative_path: str) -> PathlibPath:
    """解析项目资源路径并确保其位于允许的目录下。"""

    candidate = (HOST_TRAINING_PATH / relative_path).resolve()
    try:
        candidate.relative_to(HOST_TRAINING_PATH)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"资源路径无效：仅允许访问位于 {HOST_TRAINING_PATH} 下的文件或目录。"
            ),
        ) from exc
    return candidate


def _ensure_project_assets_available(project: ProjectDetail) -> None:
    """确认训练所需的数据集与配置文件均已存在。"""

    missing: List[str] = []
    dataset_path = _resolve_project_asset(project.dataset_name)
    if not dataset_path.exists():
        missing.append(f"数据集 {project.dataset_name}")
    yaml_path = _resolve_project_asset(project.training_yaml_name)
    if not yaml_path.exists():
        missing.append(f"训练配置 {project.training_yaml_name}")
    if missing:
        raise HTTPException(
            status_code=400,
            detail="以下项目资源尚未上传完成：" + "、".join(missing),
        )


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(
    payload: ProjectCreate, store: FileStorage = Depends(get_storage)
) -> ProjectDetail:
    """创建新的训练项目（功能点 5.2.1）。"""

    project = store.create_project(payload)
    return project


@router.get("", response_model=List[Project])
def list_projects(store: FileStorage = Depends(get_storage)) -> List[Project]:
    """列出所有训练项目。"""

    return list(store.list_projects())


@router.post("/{project_reference}/runs", response_model=RunDetail, status_code=201)
def create_run(
    project_reference: str = PathParam(
        ..., description="Project identifier or unique name"
    ),
    store: FileStorage = Depends(get_storage),
) -> RunDetail:
    """在指定项目下启动新的训练运行（功能点 5.2.3）。"""

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
            host_training_dir=HOST_TRAINING_DIR,
            docker_container_name=DOCKER_CONTAINER_NAME,
            docker_working_dir=DOCKER_WORKING_DIR,
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
                message=(f"已触发训练命令：{start_command} (PID {process.pid})"),
            )
        ],
    )
    run = store.update_run_status(run.id, RunStatus.RUNNING, progress=0.05)
    return run
