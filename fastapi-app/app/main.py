"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from . import config
from .logging import setup_logging
from src.utils.filesystem import ensure_directories
from src.features.datasets.api import router as datasets_router
from src.features.deid.api import router as deid_router
from src.features.deployments.api import router as deployments_router
from src.features.health.api import router as health_router
from src.features.projects.api import router as projects_router
from src.features.train_configs.api import router as train_configs_router


def create_app() -> FastAPI:
    """Application factory used by entrypoints and tests."""

    setup_logging()
    ensure_directories(
        config.BASE_STORAGE_DIR,
        config.DATASET_DIR,
        config.FILES_DIR,
        config.UPLOADS_DIR,
        config.TRAIN_CONFIG_DIR,
        config.DEPLOY_LOG_DIR,
    )

    app = FastAPI(title="LLM Platform API")
    app.include_router(health_router)
    app.include_router(deid_router, prefix=config.API_PREFIX)
    app.include_router(datasets_router, prefix=config.API_PREFIX)
    app.include_router(train_configs_router, prefix=config.API_PREFIX)
    app.include_router(deployments_router, prefix=config.API_PREFIX)
    app.include_router(projects_router, prefix=config.API_PREFIX)
    return app


__all__ = ["create_app"]
