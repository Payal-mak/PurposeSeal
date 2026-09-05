from fastapi import APIRouter

from ..core.config import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "app_name": settings.app_name}
