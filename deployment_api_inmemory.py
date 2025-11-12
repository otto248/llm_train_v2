# deployment_api_inmemory.py
"""
纯内存版 Model Deployment API (FastAPI)

说明:
- 所有部署信息仅保存在内存（重启后丢失）。
- 功能：
  1) 创建部署: POST /deployments
  2) 查询部署状态: GET /deployments/{deployment_id}
  3) 下线/删除部署: DELETE /deployments/{deployment_id}
  4) 列表与筛选: GET /deployments?model=...&tag=...&status=...

启动:
    pip install fastapi uvicorn requests python-multipart
    uvicorn deployment_api_inmemory:app --reload --port 8500
"""

import os
import time
import uuid
import socket
import signal
import subprocess
from typing import Optional, List, Dict, Any
from threading import Lock

import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

# Optional pynvml
try:
    import pynvml
    PYNVML_AVAILABLE = True
except Exception:
    PYNVML_AVAILABLE = False

# Config (可按需修改)
VLLM_CMD_TEMPLATE = os.environ.get(
    "VLLM_CMD_TEMPLATE",
    "vllm --model {model_path} --http-port {port} --device-ids {gpu_id} {extra_args}"
)
PORT_RANGE = (8000, 8999)
DEFAULT_HEALTH_PATH = "/health"
HTTP_CHECK_TIMEOUT = 2.0
PROCESS_TERMINATE_TIMEOUT = 10.0
LOG_DIR = os.environ.get("DEPLOY_LOG_DIR", "./deploy_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Models
class CreateDeploymentRequest(BaseModel):
    model_path: str
    model_version: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    extra_args: Optional[str] = Field("", description="传给 vllm 的额外参数")
    preferred_gpu: Optional[int] = None
    health_path: Optional[str] = DEFAULT_HEALTH_PATH

class DeploymentInfo(BaseModel):
    deployment_id: str
    model_path: str
    model_version: Optional[str]
    tags: List[str]
    gpu_id: Optional[int]
    port: int
    pid: Optional[int]
    status: str  # running / stopped / starting / failed / stopping
    started_at: Optional[float]
    stopped_at: Optional[float]
    health_ok: Optional[bool]
    vllm_cmd: Optional[str]
    log_file: Optional[str]
    health_path: Optional[str]

# In-memory store
_store_lock = Lock()
_deployments: Dict[str, Dict[str, Any]] = {}  # deployment_id -> info

# GPU utilities
def get_gpu_free_memory():
    results = []
    if PYNVML_AVAILABLE:
        try:
            pynvml.nvmlInit()
            cnt = pynvml.nvmlDeviceGetCount()
            for i in range(cnt):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                results.append((i, mem.free))
            pynvml.nvmlShutdown()
            return results
        except Exception:
            pass
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,memory.free", "--format=csv,noheader,nounits"],
            text=True
        )
        for line in out.strip().splitlines():
            idx, mem = [s.strip() for s in line.split(",")]
            results.append((int(idx), int(mem) * 1024 * 1024))
    except Exception:
        pass
    return results

def pick_gpu(preferred: Optional[int] = None) -> Optional[int]:
    gpus = get_gpu_free_memory()
    if not gpus:
        return None
    if preferred is not None:
        for gid, _ in gpus:
            if gid == preferred:
                return gid
    # choose gpu with max free memory
    gpus_sorted = sorted(gpus, key=lambda x: x[1], reverse=True)
    return gpus_sorted[0][0]

# Port utilities
def is_port_free(port: int, host: str = "127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.bind((host, port))
            return True
        except Exception:
            return False

def find_free_port(low=PORT_RANGE[0], high=PORT_RANGE[1]):
    for p in range(low, high + 1):
        if is_port_free(p):
            return p
    raise RuntimeError("no free port available")

# Start/stop process
def start_vllm_process(model_path: str, port: int, gpu_id: Optional[int], extra_args: str, log_file_path: str) -> subprocess.Popen:
    env = os.environ.copy()
    if gpu_id is None:
        env.pop("CUDA_VISIBLE_DEVICES", None)
    else:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    cmd = VLLM_CMD_TEMPLATE.format(model_path=model_path, port=port, gpu_id=(gpu_id if gpu_id is not None else ""), extra_args=extra_args or "")
    logfile = open(log_file_path, "a", encoding="utf-8")
    # 注意：使用 shell=True 以便模板灵活，生产请谨慎并做输入校验
    popen = subprocess.Popen(cmd, shell=True, stdout=logfile, stderr=subprocess.STDOUT, env=env, preexec_fn=os.setsid)
    return popen

def check_http_health(port: int, path: str = DEFAULT_HEALTH_PATH) -> bool:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        r = requests.get(url, timeout=HTTP_CHECK_TIMEOUT)
        return r.status_code == 200
    except Exception:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/", timeout=HTTP_CHECK_TIMEOUT)
            return r.status_code == 200
        except Exception:
            return False

# FastAPI app
app = FastAPI(title="In-memory Model Deployment API")

@app.post("/deployments", response_model=DeploymentInfo)
def create_deployment(req: CreateDeploymentRequest, background_tasks: BackgroundTasks):
    model_path = req.model_path
    gpu_id = pick_gpu(req.preferred_gpu)
    try:
        port = find_free_port()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    deployment_id = str(uuid.uuid4())
    started_at = time.time()
    log_file = os.path.join(LOG_DIR, f"{deployment_id}.log")
    vllm_cmd = VLLM_CMD_TEMPLATE.format(model_path=model_path, port=port, gpu_id=(gpu_id if gpu_id is not None else ""), extra_args=req.extra_args or "")

    try:
        popen = start_vllm_process(model_path=model_path, port=port, gpu_id=gpu_id, extra_args=req.extra_args or "", log_file_path=log_file)
        pid = popen.pid
    except Exception as e:
        pid = None
        with _store_lock:
            _deployments[deployment_id] = {
                "deployment_id": deployment_id,
                "model_path": model_path,
                "model_version": req.model_version,
                "tags": req.tags or [],
                "gpu_id": gpu_id,
                "port": port,
                "pid": None,
                "status": "failed",
                "started_at": started_at,
                "stopped_at": time.time(),
                "health_ok": False,
                "vllm_cmd": vllm_cmd,
                "log_file": log_file,
                "health_path": req.health_path or DEFAULT_HEALTH_PATH
            }
        raise HTTPException(status_code=500, detail=f"failed to start process: {e}")

    with _store_lock:
        _deployments[deployment_id] = {
            "deployment_id": deployment_id,
            "model_path": model_path,
            "model_version": req.model_version,
            "tags": req.tags or [],
            "gpu_id": gpu_id,
            "port": port,
            "pid": pid,
            "status": "starting",
            "started_at": started_at,
            "stopped_at": None,
            "health_ok": False,
            "vllm_cmd": vllm_cmd,
            "log_file": log_file,
            "health_path": req.health_path or DEFAULT_HEALTH_PATH
        }

    def _background_health_check(dep_id: str, pid_val: int, port_val: int, health_path: str):
        time.sleep(1.0)
        # check process existence
        try:
            os.kill(pid_val, 0)
        except Exception:
            with _store_lock:
                dep = _deployments.get(dep_id)
                if dep:
                    dep["status"] = "stopped"
                    dep["health_ok"] = False
                    dep["stopped_at"] = time.time()
            return
        success = False
        for _ in range(12):  # try ~6s
            if check_http_health(port_val, health_path):
                success = True
                break
            time.sleep(0.5)
        with _store_lock:
            dep = _deployments.get(dep_id)
            if dep:
                dep["status"] = "running"
                dep["health_ok"] = success

    background_tasks.add_task(_background_health_check, deployment_id, pid, port, req.health_path or DEFAULT_HEALTH_PATH)
    return DeploymentInfo(**_deployments[deployment_id])

@app.get("/deployments/{deployment_id}", response_model=DeploymentInfo)
def get_deployment(deployment_id: str):
    with _store_lock:
        info = _deployments.get(deployment_id)
        if not info:
            raise HTTPException(status_code=404, detail="deployment not found")
        pid = info.get("pid")
    if pid:
        alive = True
        try:
            os.kill(pid, 0)
        except Exception:
            alive = False
        with _store_lock:
            info["status"] = "running" if alive else "stopped"
            if alive:
                info["health_ok"] = check_http_health(info["port"], info.get("health_path", DEFAULT_HEALTH_PATH))
            else:
                info["health_ok"] = False
    return DeploymentInfo(**info)

@app.delete("/deployments/{deployment_id}")
def delete_deployment(deployment_id: str, force: Optional[bool] = False):
    with _store_lock:
        info = _deployments.get(deployment_id)
        if not info:
            raise HTTPException(status_code=404, detail="deployment not found")
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
        while time.time() - start < PROCESS_TERMINATE_TIMEOUT:
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
                with _store_lock:
                    _deployments[deployment_id]["status"] = "stopping"
                raise HTTPException(status_code=409, detail="process did not stop within timeout; retry with force=true")

    with _store_lock:
        rec = _deployments.pop(deployment_id, None)
        if rec:
            rec["status"] = "stopped"
            rec["stopped_at"] = time.time()

    return {"detail": "deployment removed", "deployment_id": deployment_id}

@app.get("/deployments", response_model=List[DeploymentInfo])
def list_deployments(model: Optional[str] = None, tag: Optional[str] = None, status: Optional[str] = None):
    res = []
    with _store_lock:
        # refresh running statuses' health
        for info in _deployments.values():
            pid = info.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    info["status"] = "running"
                    info["health_ok"] = check_http_health(info["port"], info.get("health_path", DEFAULT_HEALTH_PATH))
                except Exception:
                    info["status"] = "stopped"
                    info["health_ok"] = False
        for info in _deployments.values():
            if model and model not in (info.get("model_path") or ""):
                continue
            if tag and tag not in (info.get("tags") or []):
                continue
            if status and (info.get("status") or "").lower() != status.lower():
                continue
            res.append(DeploymentInfo(**info))
    return res

@app.get("/_internal/health")
def internal_health():
    return {"status": "ok", "time": time.time()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("deployment_api_inmemory:app", host="0.0.0.0", port=8500, reload=True)
