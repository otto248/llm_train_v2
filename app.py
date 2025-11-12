from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
import os
import json
import re
import random
from pathlib import Path
from datetime import datetime, timezone

# ============ 配置 ============
STORAGE_ROOT = Path("storage")
DATASET_DIR = STORAGE_ROOT / "datasets"
FILES_DIR = STORAGE_ROOT / "files"
UPLOADS_DIR = STORAGE_ROOT / "uploads"
TRAIN_CONFIG_DIR = STORAGE_ROOT / "train_configs"  
MAX_SMALL_FILE_BYTES = 100 * 1024 * 1024  # 100MB
MAX_YAML_BYTES = 5 * 1024 * 1024  # 5MB
POLICY_VERSION = "2025-10-01"

for p in (DATASET_DIR, FILES_DIR, UPLOADS_DIR, TRAIN_CONFIG_DIR):
    p.mkdir(parents=True, exist_ok=True)

# ============ 脱敏策略扩展点 ============
class DeidStrategy:
    """抽象基类：实现 `deidentify_texts(texts, options) -> (deid_texts, mapping_list)`"""
    def deidentify_texts(self, texts: List[str], options: Dict[str, Any]) -> (List[str], List[Dict[str, Any]]):
        raise NotImplementedError

STRATEGY_REGISTRY: Dict[str, DeidStrategy] = {}

def register_strategy(name: str):
    def deco(cls):
        STRATEGY_REGISTRY[name] = cls()
        return cls
    return deco

@register_strategy("default")
class RandomDigitReplacement(DeidStrategy):
    """
    默认脱敏策略：把所有数字字符（0-9）替换为随机数字。
    支持 options.seed（int）以保证可复现。
    返回 mapping：记录每个原始数字串 -> 替换后字符串（唯一列表）。
    """
    DIGIT_RE = re.compile(r"\d+")
    def deidentify_texts(self, texts: List[str], options: Dict[str, Any]):
        seed = options.get("seed")
        rnd = random.Random(seed)
        mapping = {}  # original_str -> pseudo_str
        def repl(match):
            s = match.group(0)
            if s in mapping:
                return mapping[s]
            # produce replacement preserving length (digit-by-digit random)
            repl_digits = "".join(str(rnd.randint(0,9)) for _ in range(len(s)))
            mapping[s] = repl_digits
            return repl_digits
        out_texts = []
        for t in texts:
            newt = self.DIGIT_RE.sub(repl, t)
            out_texts.append(newt)
        mapping_list = [{"type":"NUMBER","original":k,"pseudo":v} for k,v in mapping.items()]
        return out_texts, mapping_list

# 以后可以 register_strategy("other_policy") 来添加更多策略。

# ============ Data models ============
class DeidRequestOptions(BaseModel):
    locale: Optional[str] = "zh-CN"
    format: Optional[str] = "text"
    return_mapping: Optional[bool] = False
    seed: Optional[int] = None

class DeidRequest(BaseModel):
    policy_id: Optional[str] = "default"
    text: List[str]
    options: Optional[DeidRequestOptions] = DeidRequestOptions()

class DeidResponse(BaseModel):
    deidentified: List[str]
    mapping: Optional[List[Dict[str, str]]] = None
    policy_version: str

# Dataset models
class DatasetCreateRequest(BaseModel):
    name: str
    dtype: Optional[str] = Field(None, alias="type")  # type is reserved in python
    source: Optional[str] = None
    task_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class DatasetRecord(BaseModel):
    id: str
    name: str
    type: Optional[str] = None
    source: Optional[str] = None
    task_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: str
    status: str
    files: List[Dict[str, Any]] = []

# ============ Helpers: persistence ============
def dataset_path(dataset_id: str) -> Path:
    return DATASET_DIR / f"{dataset_id}.json"

def save_dataset_record(record: Dict[str, Any]):
    p = dataset_path(record["id"])
    with open(p, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

def load_dataset_record(dataset_id: str) -> Dict[str, Any]:
    p = dataset_path(dataset_id)
    if not p.exists():
        raise FileNotFoundError()
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

# ============ FastAPI app ============
app = FastAPI(title="Dataset + Deidentify Service")

# ----- Deidentify endpoint -----
@app.post("/v1/deidentify:test", response_model=DeidResponse)
def deidentify(req: DeidRequest):
    policy_id = req.policy_id or "default"
    strategy = STRATEGY_REGISTRY.get(policy_id)
    if strategy is None:
        raise HTTPException(status_code=400, detail=f"Unknown policy_id '{policy_id}'")
    options = req.options.model_dump() if req.options else {}
    deid_texts, mapping_list = strategy.deidentify_texts(req.text, options)
    response = {"deidentified": deid_texts, "policy_version": POLICY_VERSION}
    if options.get("return_mapping"):
        response["mapping"] = mapping_list
    else:
        response["mapping"] = None
    return response

# ----- Dataset APIs -----
@app.post("/v1/datasets")
def create_dataset(req: DatasetCreateRequest):
    dataset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "") + "Z"
    rec = {
        "id": dataset_id,
        "name": req.name,
        "type": req.dtype,
        "source": req.source,
        "task_type": req.task_type,
        "metadata": req.metadata or {},
        "created_at": now,
        "status": "created",
        "files": []
    }
    save_dataset_record(rec)
    return {"id": dataset_id, "created_at": now}

@app.get("/v1/datasets/{dataset_id}")
def get_dataset(dataset_id: str):
    try:
        rec = load_dataset_record(dataset_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dataset not found")
    # compute simple upload progress: files count
    rec["upload_progress"] = {"files_count": len(rec.get("files", []))}
    return rec

@app.put("/v1/datasets/{dataset_id}/files")
async def upload_small_file(dataset_id: str, file: UploadFile = File(...)):
    # validate dataset
    try:
        rec = load_dataset_record(dataset_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dataset not found")
    # read content to estimate size (small files only)
    content = await file.read()
    size = len(content)
    if size > MAX_SMALL_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Limit is {MAX_SMALL_FILE_BYTES} bytes")
    # save to files dir with unique name
    upload_id = str(uuid.uuid4())
    filename = f"{upload_id}_{file.filename}"
    file_path = FILES_DIR / filename
    with open(file_path, "wb") as f:
        f.write(content)
    # record upload session in uploads dir
    upload_record = {
        "upload_id": upload_id,
        "dataset_id": dataset_id,
        "filename": file.filename,
        "stored_filename": filename,
        "bytes": size,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "") + "Z",
        "status": "completed"
    }
    with open(UPLOADS_DIR / f"{upload_id}.json", "w", encoding="utf-8") as f:
        json.dump(upload_record, f, ensure_ascii=False, indent=2)
    # append file info to dataset record
    file_entry = {
        "upload_id": upload_id,
        "name": file.filename,
        "stored_name": filename,
        "bytes": size,
        "uploaded_at": upload_record["created_at"],
    }
    rec["files"].append(file_entry)
    rec["status"] = "ready"
    save_dataset_record(rec)
    return {"upload_id": upload_id, "dataset_id": dataset_id, "bytes": size, "filename": file.filename}

@app.delete("/v1/uploads/{upload_id}")
def abort_upload(upload_id: str):
    upload_meta_path = UPLOADS_DIR / f"{upload_id}.json"
    if not upload_meta_path.exists():
        raise HTTPException(status_code=404, detail="Upload session not found")
    with open(upload_meta_path, "r", encoding="utf-8") as f:
        upload_record = json.load(f)
    # remove stored file if exists
    stored_filename = upload_record.get("stored_filename")
    if stored_filename:
        file_path = FILES_DIR / stored_filename
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception as e:
                # log but continue
                pass 
    # remove upload meta
    try:
        upload_meta_path.unlink()
    except Exception:
        pass
    # remove from dataset's files list
    dsid = upload_record.get("dataset_id")
    if dsid:
        try:
            rec = load_dataset_record(dsid)
            original_len = len(rec.get("files", []))
            rec["files"] = [f for f in rec.get("files", []) if f.get("upload_id") != upload_id]
            if len(rec["files"]) != original_len:
                save_dataset_record(rec)
        except FileNotFoundError:
            # dataset missing: ignore
            pass
    return {"upload_id": upload_id, "status": "aborted"}


# -- Train config upload
@app.put("/v1/datasets/{dataset_id}/train-config")
async def upload_train_config(dataset_id: str, file: UploadFile = File(...)):
    try:
        rec = load_dataset_record(dataset_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dataset not found")

    # 校验文件类型与大小
    if not (file.filename.endswith(".yaml") or file.filename.endswith(".yml")):
        raise HTTPException(status_code=400, detail="Only .yaml or .yml files are allowed")

    content = await file.read()
    if len(content) > MAX_YAML_BYTES:
        raise HTTPException(status_code=413, detail="YAML file too large (max 5MB)")

    config_path = TRAIN_CONFIG_DIR / f"{dataset_id}_train.yaml"
    with open(config_path, "wb") as f:
        f.write(content)

    rec["train_config"] = {
        "filename": file.filename,
        "uploaded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "") + "Z",
        "size": len(content)
    }
    rec["status"] = "train_config_uploaded"
    save_dataset_record(rec)

    return {"dataset_id": dataset_id, "train_config": rec["train_config"]}

@app.get("/v1/datasets/{dataset_id}/train-config")
def get_train_config(dataset_id: str):
    try:
        rec = load_dataset_record(dataset_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if not rec.get("train_config"):
        raise HTTPException(status_code=404, detail="Train config not uploaded yet")
    return rec["train_config"]

@app.delete("/v1/datasets/{dataset_id}/train-config")
def delete_train_config(dataset_id: str):
    try:
        rec = load_dataset_record(dataset_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dataset not found")
    config_path = TRAIN_CONFIG_DIR / f"{dataset_id}_train.yaml"
    if config_path.exists():
        config_path.unlink()
    rec["train_config"] = None
    rec["status"] = "train_config_deleted"
    save_dataset_record(rec)
    return {"dataset_id": dataset_id, "status": "train_config_deleted"}
# health
@app.get("/healthz")
def health():
    return {"status":"ok"}


if __name__ == "__main__":
    import uvicorn
    import sys
    
    # 默认端口和主机
    host = "127.0.0.1"
    port = 8000
    
    # 支持命令行参数
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:  
        port = int(sys.argv[2])
    
    print(f"Starting server on http://{host}:{port}")
    print("Press CTRL+C to stop the server")
    
    uvicorn.run(
        "app:app",  # 指向当前文件中的app实例
        host=host,
        port=port,
        reload=False,  # 开发模式下启用热重载
        log_level="info"
    )