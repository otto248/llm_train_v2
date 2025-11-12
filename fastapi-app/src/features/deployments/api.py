"""Model deployment management endpoints."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import uuid
from threading import Lock
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app import config
from src.utils.filesystem import ensure_directories

try:  # pragma: no cover - optional dependency
    import pynvml

    _PYNVML_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _PYNVML_AVAILABLE = False

router = APIRouter(prefix="/v1/deployments", tags=["deployments"])


def _init_directories() -> None:
    ensure_directories(config.DEPLOY_LOG_DIR)


class CreateDeploymentRequest(BaseModel):
    model_path: str
    model_version: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    extra_args: str = ""
    preferred_gpu: Optional[int] = None
    health_path: Optional[str] = config.DEFAULT_HEALTH_PATH


class DeploymentInfo(BaseModel):
    deployment_id: str
    model_path: str
    model_version: Optional[str]
    tags: List[str]
    gpu_id: Optional[int]
    port: int
    pid: Optional[int]
    status: str
    started_at: Optional[float]
    stopped_at: Optional[float]
    health_ok: Optional[bool]
    vllm_cmd: Optional[str]
    log_file: Optional[str]
    health_path: Optional[str]


_STORE_LOCK = Lock()
_DEPLOYMENTS: Dict[str, Dict[str, Any]] = {}


def _get_gpu_free_memory() -> List[tuple[int, int]]:
    results: List[tuple[int, int]] = []
    if _PYNVML_AVAILABLE:
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            for idx in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                results.append((idx, mem.free))
            pynvml.nvmlShutdown()
            return results
        except Exception:
            pass
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
        for line in output.strip().splitlines():
            gpu_idx, mem_free = [s.strip() for s in line.split(",")]
            results.append((int(gpu_idx), int(mem_free) * 1024 * 1024))
    except Exception:
        pass
    return results


def _pick_gpu(preferred: Optional[int] = None) -> Optional[int]:
    gpus = _get_gpu_free_memory()
    if not gpus:
        return None
    if preferred is not None:
        for idx, _ in gpus:
            if idx == preferred:
                return idx
    gpus.sort(key=lambda item: item[1], reverse=True)
    return gpus[0][0]


def _is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.bind((host, port))
            return True
        except Exception:
            return False


def _find_free_port() -> int:
    for port in range(config.PORT_RANGE_LOW, config.PORT_RANGE_HIGH + 1):
        if _is_port_free(port):
            return port
    raise RuntimeError("No free port available in configured range")


def _start_vllm_process(
    model_path: str,
    port: int,
    gpu_id: Optional[int],
    extra_args: str,
    log_file: str,
) -> subprocess.Popen:
    env = os.environ.copy()
    if gpu_id is None:
        env.pop("CUDA_VISIBLE_DEVICES", None)
    else:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    cmd = config.VLLM_CMD_TEMPLATE.format(
        model_path=model_path,
        port=port,
        gpu_id=gpu_id if gpu_id is not None else "",
        extra_args=extra_args or "",
    )
    logfile = open(log_file, "a", encoding="utf-8")
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdout=logfile,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,
    )
    return process


def _check_http_health(port: int, path: str) -> bool:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        response = requests.get(url, timeout=config.HTTP_CHECK_TIMEOUT)
        return response.status_code == 200
    except Exception:
        try:
            response = requests.get(
                f"http://127.0.0.1:{port}/", timeout=config.HTTP_CHECK_TIMEOUT
            )
            return response.status_code == 200
        except Exception:
            return False


@router.post("", response_model=DeploymentInfo, status_code=201)
def create_deployment(
    payload: CreateDeploymentRequest, background: BackgroundTasks
) -> DeploymentInfo:
    _init_directories()
    gpu_id = _pick_gpu(payload.preferred_gpu)
    try:
        port = _find_free_port()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    deployment_id = str(uuid.uuid4())
    started_at = time.time()
    log_file = str(config.DEPLOY_LOG_DIR / f"{deployment_id}.log")
    vllm_cmd = config.VLLM_CMD_TEMPLATE.format(
        model_path=payload.model_path,
        port=port,
        gpu_id=gpu_id if gpu_id is not None else "",
        extra_args=payload.extra_args or "",
    )
    try:
        process = _start_vllm_process(
            payload.model_path, port, gpu_id, payload.extra_args or "", log_file
        )
        pid = process.pid
    except Exception as exc:  # pragma: no cover - process failure path
        with _STORE_LOCK:
            _DEPLOYMENTS[deployment_id] = {
                "deployment_id": deployment_id,
                "model_path": payload.model_path,
                "model_version": payload.model_version,
                "tags": payload.tags or [],
                "gpu_id": gpu_id,
                "port": port,
                "pid": None,
                "status": "failed",
                "started_at": started_at,
                "stopped_at": time.time(),
                "health_ok": False,
                "vllm_cmd": vllm_cmd,
                "log_file": log_file,
                "health_path": payload.health_path or config.DEFAULT_HEALTH_PATH,
            }
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    with _STORE_LOCK:
        _DEPLOYMENTS[deployment_id] = {
            "deployment_id": deployment_id,
            "model_path": payload.model_path,
            "model_version": payload.model_version,
            "tags": payload.tags or [],
            "gpu_id": gpu_id,
            "port": port,
            "pid": pid,
            "status": "starting",
            "started_at": started_at,
            "stopped_at": None,
            "health_ok": False,
            "vllm_cmd": vllm_cmd,
            "log_file": log_file,
            "health_path": payload.health_path or config.DEFAULT_HEALTH_PATH,
        }

    def _background_health_check(deployment_id: str, pid: int, port: int, path: str) -> None:
        time.sleep(1.0)
        try:
            os.kill(pid, 0)
        except Exception:
            with _STORE_LOCK:
                info = _DEPLOYMENTS.get(deployment_id)
                if info:
                    info["status"] = "stopped"
                    info["health_ok"] = False
                    info["stopped_at"] = time.time()
            return
        healthy = False
        for _ in range(12):
            if _check_http_health(port, path):
                healthy = True
                break
            time.sleep(0.5)
        with _STORE_LOCK:
            info = _DEPLOYMENTS.get(deployment_id)
            if info:
                info["status"] = "running"
                info["health_ok"] = healthy

    background.add_task(
        _background_health_check,
        deployment_id,
        pid,
        port,
        payload.health_path or config.DEFAULT_HEALTH_PATH,
    )
    return DeploymentInfo(**_DEPLOYMENTS[deployment_id])


@router.get("/{deployment_id}", response_model=DeploymentInfo)
def get_deployment(deployment_id: str) -> DeploymentInfo:
    with _STORE_LOCK:
        info = _DEPLOYMENTS.get(deployment_id)
        if not info:
            raise HTTPException(status_code=404, detail="Deployment not found")
        pid = info.get("pid")
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except Exception:
            alive = False
        with _STORE_LOCK:
            info = _DEPLOYMENTS.get(deployment_id)
            if info:
                info["status"] = "running" if alive else "stopped"
                info["health_ok"] = (
                    _check_http_health(info["port"], info.get("health_path", config.DEFAULT_HEALTH_PATH))
                    if alive
                    else False
                )
    return DeploymentInfo(**info)


@router.delete("/{deployment_id}")
def delete_deployment(deployment_id: str, force: bool = False) -> Dict[str, Any]:
    with _STORE_LOCK:
        info = _DEPLOYMENTS.get(deployment_id)
        if not info:
            raise HTTPException(status_code=404, detail="Deployment not found")
        pid = info.get("pid")
        info["status"] = "stopping"

    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        start = time.time()
        stopped = False
        while time.time() - start < config.PROCESS_TERMINATE_TIMEOUT:
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except Exception:
                stopped = True
                break
        if not stopped:
            if force:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
            else:
                with _STORE_LOCK:
                    _DEPLOYMENTS[deployment_id]["status"] = "stopping"
                raise HTTPException(
                    status_code=409,
                    detail="Process did not stop within timeout; retry with force=true",
                )

    with _STORE_LOCK:
        record = _DEPLOYMENTS.pop(deployment_id, None)
        if record:
            record["status"] = "stopped"
            record["stopped_at"] = time.time()
    return {"detail": "deployment removed", "deployment_id": deployment_id}


@router.get("", response_model=List[DeploymentInfo])
def list_deployments(
    model: Optional[str] = None,
    tag: Optional[str] = None,
    status: Optional[str] = None,
) -> List[DeploymentInfo]:
    results: List[DeploymentInfo] = []
    with _STORE_LOCK:
        for info in _DEPLOYMENTS.values():
            pid = info.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    info["status"] = "running"
                    info["health_ok"] = _check_http_health(
                        info["port"], info.get("health_path", config.DEFAULT_HEALTH_PATH)
                    )
                except Exception:
                    info["status"] = "stopped"
                    info["health_ok"] = False
            if model and model not in info.get("model_path", ""):
                continue
            if tag and tag not in info.get("tags", []):
                continue
            if status and (info.get("status") or "").lower() != status.lower():
                continue
            results.append(DeploymentInfo(**info))
    return results


@router.get("/_internal/health")
def internal_health() -> Dict[str, Any]:
    return {"status": "ok", "time": time.time()}
