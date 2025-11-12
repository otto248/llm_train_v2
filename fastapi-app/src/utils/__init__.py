"""Utility helpers for process management."""

from __future__ import annotations

import logging
import shlex
import subprocess
from typing import Optional

from .filesystem import ensure_directories


def launch_training_process(
    command: str,
    *,
    host_training_dir: str,
    docker_container_name: str,
    docker_working_dir: str,
    log: Optional[logging.Logger] = None,
) -> subprocess.Popen:
    """Launch the training process via docker exec.

    The implementation shells out to docker to keep the example simple. In a
    production-ready setup this should be replaced with a task runner or job
    scheduler integration.
    """

    ensure_directories(host_training_dir)
    docker_command = [
        "docker",
        "exec",
        docker_container_name,
        "bash",
        "-lc",
        f"cd {shlex.quote(docker_working_dir)} && {command}",
    ]
    if log:
        log.info("Launching training command: %s", command)
    try:
        process = subprocess.Popen(docker_command)
    except OSError as exc:  # pragma: no cover - depends on environment
        if log:
            log.error("Failed to launch training command: %s", exc)
        raise RuntimeError("failed to launch training process") from exc
    return process


__all__ = ["launch_training_process"]
