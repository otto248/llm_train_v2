"""Application configuration constants."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

# Storage roots -------------------------------------------------------------
BASE_STORAGE_DIR = Path(os.environ.get("LLM_APP_STORAGE", "./storage"))
DATASET_DIR = BASE_STORAGE_DIR / "datasets"
FILES_DIR = BASE_STORAGE_DIR / "files"
UPLOADS_DIR = BASE_STORAGE_DIR / "uploads"
TRAIN_CONFIG_DIR = BASE_STORAGE_DIR / "train_configs"
_DEFAULT_METADATA_DB_PATH = BASE_STORAGE_DIR / "metadata.db"
METADATA_DB_PATH = Path(os.environ.get("METADATA_DB_PATH", _DEFAULT_METADATA_DB_PATH))


def _goldendb_url_from_env() -> str | None:
    """Build a GoldenDB/MySQL SQLAlchemy URL from granular env vars.

    Allows deployments to specify credentials with ``GOLDENDB_*`` variables
    instead of crafting the SQLAlchemy URL manually. Returns ``None`` when
    the minimum set of variables is not present.
    """

    host = os.environ.get("GOLDENDB_HOST")
    user = os.environ.get("GOLDENDB_USER")
    database = os.environ.get("GOLDENDB_DATABASE") or os.environ.get("GOLDENDB_DB")
    if not (host and user and database):
        return None

    password = os.environ.get("GOLDENDB_PASSWORD")
    port = os.environ.get("GOLDENDB_PORT", "3306")
    driver = os.environ.get("GOLDENDB_DRIVER", "mysql+pymysql")

    if password:
        credentials = f"{quote_plus(user)}:{quote_plus(password)}"
    else:
        credentials = quote_plus(user)

    return f"{driver}://{credentials}@{host}:{port}/{database}"


_DEFAULT_METADATA_DATABASE_URL = f"sqlite:///{METADATA_DB_PATH}"
METADATA_DATABASE_URL = (
    os.environ.get("METADATA_DATABASE_URL")
    or _goldendb_url_from_env()
    or _DEFAULT_METADATA_DATABASE_URL
)
DEPLOY_LOG_DIR = Path(os.environ.get("DEPLOY_LOG_DIR", "./deploy_logs"))

# File limits ---------------------------------------------------------------
MAX_SMALL_FILE_BYTES = 100 * 1024 * 1024  # 100MB
MAX_YAML_BYTES = 5 * 1024 * 1024  # 5MB

# De-identification ---------------------------------------------------------
DEFAULT_DEID_POLICY_ID = "default"
DEID_POLICY_VERSION = "2025-10-01"

# Training environment ------------------------------------------------------
HOST_TRAINING_DIR = os.environ.get("HOST_TRAINING_DIR", "./training")
HOST_TRAINING_PATH = Path(HOST_TRAINING_DIR).resolve()
DOCKER_CONTAINER_NAME = os.environ.get("TRAINING_CONTAINER_NAME", "llm-training")
DOCKER_WORKING_DIR = os.environ.get("TRAINING_WORKDIR", "/workspace")

# Deployment ---------------------------------------------------------------
VLLM_CMD_TEMPLATE = os.environ.get(
    "VLLM_CMD_TEMPLATE",
    "vllm --model {model_path} --http-port {port} --device-ids {gpu_id} {extra_args}",
)
PORT_RANGE_LOW = int(os.environ.get("DEPLOY_PORT_LOW", "8000"))
PORT_RANGE_HIGH = int(os.environ.get("DEPLOY_PORT_HIGH", "8999"))
DEFAULT_HEALTH_PATH = os.environ.get("DEPLOY_HEALTH_PATH", "/health")
HTTP_CHECK_TIMEOUT = float(os.environ.get("DEPLOY_HTTP_TIMEOUT", "2.0"))
PROCESS_TERMINATE_TIMEOUT = float(os.environ.get("DEPLOY_TERMINATE_TIMEOUT", "10.0"))

# Misc ----------------------------------------------------------------------
API_PREFIX = "/api"

__all__ = [
    "API_PREFIX",
    "BASE_STORAGE_DIR",
    "DATASET_DIR",
    "FILES_DIR",
    "UPLOADS_DIR",
    "TRAIN_CONFIG_DIR",
    "METADATA_DB_PATH",
    "METADATA_DATABASE_URL",
    "MAX_SMALL_FILE_BYTES",
    "MAX_YAML_BYTES",
    "DEFAULT_DEID_POLICY_ID",
    "DEID_POLICY_VERSION",
    "HOST_TRAINING_DIR",
    "HOST_TRAINING_PATH",
    "DOCKER_CONTAINER_NAME",
    "DOCKER_WORKING_DIR",
    "VLLM_CMD_TEMPLATE",
    "PORT_RANGE_LOW",
    "PORT_RANGE_HIGH",
    "DEFAULT_HEALTH_PATH",
    "HTTP_CHECK_TIMEOUT",
    "PROCESS_TERMINATE_TIMEOUT",
    "DEPLOY_LOG_DIR",
]
