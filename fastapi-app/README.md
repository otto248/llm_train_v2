# LLM Platform FastAPI 服务文档

本项目提供一个围绕大模型数据集管理、训练、部署与脱敏的 FastAPI 服务。所有业务接口均通过统一前缀 `/api` 暴露，除非另行说明。可选的环境变量、目录结构等配置可在 `app/config.py` 中调整。 【F:fastapi-app/app/config.py†L8-L68】

## 训练任务文件准备概览

平台默认将所有训练资源挂载到 `HOST_TRAINING_DIR`（默认 `./training`）下，并在此目录中查找项目引用的数据文件、训练配置与启动脚本。【F:fastapi-app/app/config.py†L25-L31】【F:fastapi-app/src/features/projects/api.py†L70-L108】建议在宿主机或共享存储中采用如下目录结构，便于通过 API 进行引用：

```
training/
├── datasets/
│   ├── sft_demo.jsonl
│   └── rl_pairs.jsonl
├── configs/
│   ├── sft_config.yaml
│   ├── lora_config.yaml
│   └── rl_config.yaml
└── scripts/
    ├── run_train_full_sft.sh
    ├── run_train_full_lora.sh
    └── run_train_full_rl.sh
```

- 通过 `/api/v1/datasets` 上传的数据文件最终也会落盘在 `BASE_STORAGE_DIR/datasets` 下，可通过文件名在训练项目中引用。【F:fastapi-app/app/config.py†L8-L27】【F:fastapi-app/src/features/datasets/api.py†L106-L150】
- 训练配置 YAML 可由 `/api/v1/datasets/{dataset_id}/train-config` 接口上传，系统会同步更新元信息中的 `training_yaml_name` 并在触发训练时进行校验。【F:fastapi-app/src/features/train_configs/api.py†L17-L61】【F:fastapi-app/src/features/projects/api.py†L70-L108】
- 默认情况下，项目运行会拼接命令 `bash run_train_full_sft.sh <training_yaml_name>`，因此需在 `scripts/` 中准备同名入口；如需支持 LoRA/RLHF，可根据项目约定扩展启动脚本并在 YAML 内保持一致，以便日志中可见真实命令。【F:fastapi-app/src/features/projects/api.py†L19-L108】

### 模型输入输出与文件格式约定

训练接口通过 `ProjectCreate` 请求体接收以下字段：`name`、`dataset_name`、`training_yaml_name`、`description`（可选）。接口返回 `ProjectDetail` 与 `RunDetail`，其中会记录运行状态、日志与 `start_command`，便于追踪不同训练范式的执行情况。【F:fastapi-app/src/models/__init__.py†L22-L57】【F:fastapi-app/src/features/projects/api.py†L18-L108】

为确保 SFT、LoRA 与 RLHF 任务可以顺利执行，建议遵循下列文件格式与输出规范：

#### SFT（监督微调）

- **数据格式**：使用 UTF-8 编码的 `.jsonl` 文件，每行一个 JSON 对象，推荐字段：
  ```json
  {"instruction": "写一首诗", "input": "主题：春天", "output": "春风拂面..."}
  ```
  `instruction` 可选，若为空可以仅保留 `input` 与 `output`；多轮对话可将历史拼接进 `input`。
- **训练配置**：在 YAML 顶层声明 `job.type: sft`，同时提供 `model`, `training`, `evaluation`（可选）等段落，例如：
  ```yaml
  job:
    type: sft
    project: spring-poem
  model:
    base: qwen-7b
  training:
    max_steps: 1000
    learning_rate: 5e-5
    per_device_train_batch_size: 4
  ```
- **运行输出**：默认脚本 `run_train_full_sft.sh` 需在训练目录内生成模型权重、日志与指标文件（例如 `checkpoints/`、`runs/` 目录），API 侧会把 stdout/stderr 采集成 `RunDetail.logs` 供前端展示。【F:fastapi-app/src/models/__init__.py†L35-L57】

#### LoRA 适配

- **数据格式**：沿用 SFT 的 `.jsonl` 结构，确保字段名一致，便于共用数据预处理流水线。
- **训练配置**：在 YAML 中声明 `job.type: lora`，并新增 `lora` 段落描述秩、α 等超参：
  ```yaml
  job:
    type: lora
  model:
    base: qwen-7b
  lora:
    r: 8
    alpha: 16
    target_modules: [query_key_value]
  training:
    max_steps: 800
  ```
  如需合并权重，可在脚本中处理并在日志中输出生成路径。
- **运行输出**：`run_train_full_lora.sh` 应至少产出 LoRA 适配权重（如 `adapter_model.bin`）和训练日志。运行过程中产生的命令会记录在 `RunDetail.start_command`，便于审计所使用的脚本与参数。【F:fastapi-app/src/features/projects/api.py†L70-L108】

#### RLHF / PPO

- **数据格式**：推荐使用 `.jsonl` 偏好对或打分数据：
  ```json
  {"prompt": "写一首俳句", "chosen": "春雨细无声", "rejected": "天气不错"}
  ```
  若使用打分样本，可改为 `{ "prompt": "...", "response": "...", "score": 0.75 }`。
- **训练配置**：在 YAML 中指定 `job.type: rl`（或更具体的 `ppo`），并提供奖励模型/参考模型：
  ```yaml
  job:
    type: rl
    method: ppo
  model:
    policy: qwen-7b-sft
    reference: qwen-7b-sft
    reward: reward-model-v1
  training:
    rollout_batch_size: 8
    kl_coeff: 0.1
  ```
  可选的 `evaluation`、`checkpoint` 段落用于控制评估及保存频率。
- **运行输出**：`run_train_full_rl.sh` 需记录关键指标（如 KL、奖励值、平均长度）到日志中，以便 API 在 `RunDetail.logs` 中回放；模型与奖励权重保存路径可写入日志或导出为约定目录结构供部署使用。【F:fastapi-app/src/models/__init__.py†L35-L57】

> **提示**：以上字段名称为平台推荐约定，后端不会强制校验，但训练容器必须能够识别这些配置。若扩展新范式，请保持 `ProjectCreate.training_yaml_name` 与实际脚本一致，同时确保脚本产出的模型、指标路径在日志或返回值中可追踪。

## 快速启动

1. **准备依赖**
   ```bash
   cd fastapi-app
   python -m venv .venv
   source .venv/bin/activate  # Windows 使用 .venv\\Scripts\\activate
   pip install -r requirements.txt
   ```
   依赖列表可在 `requirements.txt` 中查看。 【F:fastapi-app/requirements.txt†L1-L5】
   > **提示**：若在受限网络环境中无法下载依赖，可直接使用系统自带的 Python 环境运行 `python main.py`，前提是其已经预装 FastAPI/UVicorn。
2. **运行服务**
   - 使用 Python 模块入口：
     ```bash
     python -m fastapi-app.main
     ```
   - 或使用 `uvicorn` 工厂模式：
     ```bash
     uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
     ```
   入口脚本会创建 FastAPI 应用并监听 `0.0.0.0:8000`。 【F:fastapi-app/main.py†L1-L15】
3. **验证服务已启动**
   ```bash
   curl http://localhost:8000/healthz
   ```
   如返回 `{"status": "ok", ...}` 则说明项目已成功运行。

## 健康检查

### `GET /healthz`
- **功能**：判断服务是否存活，返回当前时间戳。 【F:fastapi-app/src/features/health/api.py†L1-L14】
- **入参**：无。
- **出参**：`{"status": "ok", "time": <float>}`。
- **调用示例**：
  ```bash
  curl http://localhost:8000/healthz
  ```

## 数据集与文件上传
接口统一前缀：`/api/v1/datasets`。

### `POST /api/v1/datasets`
- **功能**：创建新的数据集元数据记录。 【F:fastapi-app/src/features/datasets/api.py†L20-L95】
- **入参**（JSON）：
  ```json
  {
    "name": "数据集名称",
    "type": "可选，数据类型",
    "source": "可选，来源",
    "task_type": "可选，任务类型",
    "metadata": {
      "description": "可选，数据集描述",
      "version": "可选，版本号",
      "records": 10000,
      "license": "可选，授权信息",
      "tags": ["finance", "cn"],
      "total_files": 0,
      "total_bytes": 0,
      "has_train_config": false
    }
  }
  ```
- **出参**：`{"id": "<dataset_id>", "created_at": "ISO 时间"}`。
- **调用示例**：
  ```bash
  curl -X POST http://localhost:8000/api/v1/datasets \
    -H 'Content-Type: application/json' \
    -d '{"name": "demo-dataset", "type": "jsonl"}'
  ```

### `GET /api/v1/datasets/{dataset_id}`
- **功能**：查询指定数据集详情、已上传文件及训练配置状态。 【F:fastapi-app/src/features/datasets/api.py†L97-L103】
- **入参**：路径参数 `dataset_id`。
- **出参**：
  ```json
  {
    "id": "...",
    "name": "...",
    "type": "...",
    "source": "...",
    "task_type": "...",
    "metadata": {
      "description": "...",
      "version": "...",
      "records": 10000,
      "license": "...",
      "tags": ["finance"],
      "total_files": 1,
      "total_bytes": 123456,
      "has_train_config": false
    },
    "created_at": "...",
    "status": "...",
    "files": [
      {"upload_id": "...", "name": "原文件名", "stored_name": "内部存储名", "bytes": 123, "uploaded_at": "..."}
    ],
    "train_config": {"filename": "...", "uploaded_at": "...", "size": 456} 或 null,
    "upload_progress": {"files_count": <int>}
  }
  ```
- **调用示例**：
  ```bash
  curl http://localhost:8000/api/v1/datasets/abcd-1234
  ```

### `PUT /api/v1/datasets/{dataset_id}/files`
- **功能**：上传不超过 100MB 的数据集文件并记录到元数据。 【F:fastapi-app/src/features/datasets/api.py†L106-L150】【F:fastapi-app/app/config.py†L17-L19】
- **入参**：
  - 路径参数 `dataset_id`
  - 表单文件字段 `file`
- **出参**：`{"upload_id": "...", "dataset_id": "...", "bytes": 123, "filename": "原文件名"}`。
- **说明**：每次上传/删除文件都会自动刷新 `metadata.total_files` 与 `metadata.total_bytes`。
- **调用示例**：
  ```bash
  curl -X PUT http://localhost:8000/api/v1/datasets/abcd-1234/files \
    -F "file=@/path/to/data.jsonl"
  ```

### `DELETE /api/v1/datasets/uploads/{upload_id}`
- **功能**：撤销一次上传并删除关联文件/记录。 【F:fastapi-app/src/features/datasets/api.py†L153-L179】
- **入参**：路径参数 `upload_id`。
- **出参**：`{"upload_id": "...", "status": "aborted"}`。
- **调用示例**：
  ```bash
  curl -X DELETE http://localhost:8000/api/v1/datasets/uploads/efgh-5678
  ```

## 训练配置上传
接口统一前缀：`/api/v1/datasets/{dataset_id}/train-config`。

### `PUT /api/v1/datasets/{dataset_id}/train-config`
- **功能**：上传 YAML 训练配置文件，限制 5MB。 【F:fastapi-app/src/features/train_configs/api.py†L17-L41】【F:fastapi-app/app/config.py†L17-L19】
- **入参**：
  - 路径参数 `dataset_id`
  - 表单文件字段 `file`（扩展名 `.yaml`/`.yml`）
- **说明**：上传/删除训练配置会同步更新数据集元信息中的 `metadata.has_train_config`。
- **出参**：`{"dataset_id": "...", "train_config": {"filename": "...", "uploaded_at": "...", "size": 123}}`。
- **调用示例**：
  ```bash
  curl -X PUT http://localhost:8000/api/v1/datasets/abcd-1234/train-config \
    -F "file=@/path/to/config.yaml"
  ```

### `GET /api/v1/datasets/{dataset_id}/train-config`
- **功能**：获取训练配置元信息（若未上传返回 404）。 【F:fastapi-app/src/features/train_configs/api.py†L44-L49】
- **入参**：路径参数 `dataset_id`。
- **出参**：与 `PUT` 响应结构相同。
- **调用示例**：
  ```bash
  curl http://localhost:8000/api/v1/datasets/abcd-1234/train-config
  ```

### `DELETE /api/v1/datasets/{dataset_id}/train-config`
- **功能**：删除训练配置文件并重置状态。 【F:fastapi-app/src/features/train_configs/api.py†L52-L61】
- **入参**：路径参数 `dataset_id`。
- **出参**：`{"dataset_id": "...", "status": "train_config_deleted"}`。
- **调用示例**：
  ```bash
  curl -X DELETE http://localhost:8000/api/v1/datasets/abcd-1234/train-config
  ```

## 文本脱敏
接口统一前缀：`/api/v1`。

### `POST /api/v1/deidentify:test`
- **功能**：根据策略对文本数组执行脱敏，可选择返回映射信息。 【F:fastapi-app/src/features/deid/api.py†L16-L47】
- **入参**（JSON）：
  ```json
  {
    "policy_id": "可选，默认 default",
    "text": ["需要脱敏的文本"],
    "options": {
      "locale": "zh-CN",
      "format": "text",
      "return_mapping": false,
      "seed": null
    }
  }
  ```
- **出参**：
  ```json
  {
    "deidentified": ["脱敏后的文本"],
    "policy_version": "2025-10-01",
    "mapping": [ {"原文": "替换"} ] // 当 return_mapping=true 时返回
  }
  ```
- **调用示例**：
  ```bash
  curl -X POST http://localhost:8000/api/v1/deidentify:test \
    -H 'Content-Type: application/json' \
    -d '{"text": ["张三的手机号是13800138000"], "options": {"return_mapping": true}}'
  ```

## 模型部署管理
接口统一前缀：`/api/v1/deployments`。

### `POST /api/v1/deployments`
- **功能**：分配端口与 GPU（可选），启动 vLLM 服务进程并记录部署信息。 【F:fastapi-app/src/features/deployments/api.py†L35-L265】【F:fastapi-app/app/config.py†L31-L40】
- **入参**（JSON）：
  ```json
  {
    "model_path": "模型权重路径",
    "model_version": "可选版本",
    "tags": ["可选标签"],
    "extra_args": "附加命令行",
    "preferred_gpu": 0,
    "health_path": "/health"
  }
  ```
- **出参**：`DeploymentInfo` 对象，例如：
  ```json
  {
    "deployment_id": "...",
    "model_path": "...",
    "model_version": "...",
    "tags": [],
    "gpu_id": 0,
    "port": 8100,
    "pid": 12345,
    "status": "starting",
    "started_at": 1710000000.0,
    "stopped_at": null,
    "health_ok": false,
    "vllm_cmd": "...",
    "log_file": "./deploy_logs/<id>.log",
    "health_path": "/health"
  }
  ```
- **调用示例**：
  ```bash
  curl -X POST http://localhost:8000/api/v1/deployments \
    -H 'Content-Type: application/json' \
    -d '{"model_path": "./models/chatglm", "preferred_gpu": 0}'
  ```

### `GET /api/v1/deployments/{deployment_id}`
- **功能**：返回部署最新状态并执行一次健康检查。 【F:fastapi-app/src/features/deployments/api.py†L267-L289】
- **入参**：路径参数 `deployment_id`。
- **出参**：`DeploymentInfo`。
- **调用示例**：
  ```bash
  curl http://localhost:8000/api/v1/deployments/abcd-1234
  ```

### `DELETE /api/v1/deployments/{deployment_id}`
- **功能**：发送终止信号并移除部署；若进程未按时退出可使用 `force=true`。 【F:fastapi-app/src/features/deployments/api.py†L292-L340】
- **入参**：
  - 路径参数 `deployment_id`
  - 查询参数 `force`（可选，默认 `false`）
- **出参**：`{"detail": "deployment removed", "deployment_id": "..."}`。
- **调用示例**：
  ```bash
  curl -X DELETE 'http://localhost:8000/api/v1/deployments/abcd-1234?force=true'
  ```

### `GET /api/v1/deployments`
- **功能**：列出部署，支持按模型、标签、状态过滤，并实时刷新健康状态。 【F:fastapi-app/src/features/deployments/api.py†L343-L370】
- **入参**：查询参数 `model`、`tag`、`status`（全部可选）。
- **出参**：`DeploymentInfo` 数组。
- **调用示例**：
  ```bash
  curl 'http://localhost:8000/api/v1/deployments?status=running'
  ```

### `GET /api/v1/deployments/_internal/health`
- **功能**：部署子系统内部健康探针。 【F:fastapi-app/src/features/deployments/api.py†L373-L375】
- **入参**：无。
- **出参**：`{"status": "ok", "time": <float>}`。
- **调用示例**：
  ```bash
  curl http://localhost:8000/api/v1/deployments/_internal/health
  ```

## 项目与训练运行
接口统一前缀：`/api/projects`。

### `POST /api/projects`
- **功能**：创建项目并记录数据集/训练配置引用。 【F:fastapi-app/src/features/projects/api.py†L18-L40】
- **入参**（JSON）：
  ```json
  {
    "name": "项目名称",
    "dataset_name": "关联数据集文件名",
    "training_yaml_name": "训练配置文件名",
    "description": "可选描述"
  }
  ```
- **出参**：`ProjectDetail`（包含空的 `runs` 列表）。
- **调用示例**：
  ```bash
  curl -X POST http://localhost:8000/api/projects \
    -H 'Content-Type: application/json' \
    -d '{"name": "demo", "dataset_name": "dataset.jsonl", "training_yaml_name": "config.yaml"}'
  ```

### `GET /api/projects`
- **功能**：列出所有项目概要信息。 【F:fastapi-app/src/features/projects/api.py†L43-L45】
- **入参**：无。
- **出参**：`Project` 数组。
- **调用示例**：
  ```bash
  curl http://localhost:8000/api/projects
  ```

### 模型训练模式输入输出说明

关于 SFT、LoRA 与 RLHF 三种训练方式的文件准备、配置示例及运行输出，请参考文档开头的「训练任务文件准备概览」章节。训练接口在创建项目与触发运行时依旧使用统一的 `ProjectCreate`/`ProjectDetail`/`RunDetail` 数据模型，具体字段含义与日志呈现方式见上文说明。【F:fastapi-app/src/models/__init__.py†L22-L57】【F:fastapi-app/src/features/projects/api.py†L18-L108】

### `POST /api/projects/{project_reference}/runs`
- **功能**：为指定项目创建一次训练运行；支持使用项目 ID 或名称查找，并会校验所需数据/配置文件是否存在后通过 `docker exec` 启动训练。 【F:fastapi-app/src/features/projects/api.py†L48-L108】
- **入参**：
  - 路径参数 `project_reference`（项目 ID 或名称）
- **出参**：`RunDetail`，包含启动命令、运行状态、日志等。
- **调用示例**：
  ```bash
  curl -X POST http://localhost:8000/api/projects/demo/runs
  ```

---

如需进一步了解模型字段或存储结构，可参考 `src/models/__init__.py` 与 `src/storage/__init__.py`。 【F:fastapi-app/src/models/__init__.py†L1-L57】【F:fastapi-app/src/storage/__init__.py†L1-L148】
