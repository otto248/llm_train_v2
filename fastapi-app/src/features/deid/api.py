"""De-identification HTTP endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import config
from .services import get_strategy

router = APIRouter(prefix="/v1", tags=["deid"])


class DeidRequestOptions(BaseModel):
    locale: Optional[str] = "zh-CN"
    format: Optional[str] = "text"
    return_mapping: bool = False
    seed: Optional[int] = None


class DeidRequest(BaseModel):
    policy_id: Optional[str] = config.DEFAULT_DEID_POLICY_ID
    text: List[str]
    options: DeidRequestOptions = DeidRequestOptions()


class DeidResponse(BaseModel):
    deidentified: List[str]
    mapping: Optional[List[Dict[str, Any]]] = None
    policy_version: str


@router.post("/deidentify:test", response_model=DeidResponse)
def deidentify(payload: DeidRequest) -> DeidResponse:
    policy_id = payload.policy_id or config.DEFAULT_DEID_POLICY_ID
    try:
        strategy = get_strategy(policy_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown policy_id '{policy_id}'") from exc

    options = payload.options.model_dump()
    texts, mapping = strategy.deidentify_texts(payload.text, options)
    response = DeidResponse(deidentified=texts, policy_version=config.DEID_POLICY_VERSION)
    if options.get("return_mapping"):
        response.mapping = mapping
    return response
