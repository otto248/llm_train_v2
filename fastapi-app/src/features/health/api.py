"""Health check endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthcheck() -> dict[str, str | float]:
    return {"status": "ok", "time": time.time()}
