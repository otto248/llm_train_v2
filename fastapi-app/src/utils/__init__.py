"""Utility helpers for process management."""

from __future__ import annotations

import logging
import subprocess
from typing import Optional


def launch_training_process(
    start_command: str,
    *,
    host_training_dir: str,
    docker_container_name: str,
    docker_working_dir: str,
    log: Optional[logging.Logger] = None,
) -> subprocess.Popen[bytes]:
    """Launch a training process inside a Docker container."""

    logger = log or logging.getLogger(__name__)
    docker_command = (
        f"cd {host_training_dir} && "
        f"docker exec -i {docker_container_name} "
        "env LANG=C.UTF-8 bash -lc "
        f'"cd {docker_working_dir} && {start_command}"'
    )
    logger.info("Launching training command: %s", docker_command)
    try:
        process = subprocess.Popen(  # noqa: S603, S607 - intentional command execution
            ["bash", "-lc", docker_command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - defensive guard
        raise RuntimeError("无法执行训练命令，请检查服务器环境配置。") from exc
    return process


__all__ = ["launch_training_process"]
