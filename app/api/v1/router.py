"""Aggregates all v1 routers behind API-key auth."""

from fastapi import APIRouter, Depends

from app.api.deps import require_api_key
from app.api.v1 import profiles

api_router = APIRouter(dependencies=[Depends(require_api_key)])
api_router.include_router(profiles.router)
