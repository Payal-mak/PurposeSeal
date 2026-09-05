from fastapi import APIRouter

from ..core.config import get_settings
from .assets import router as assets_router
from .copies import router as copies_router
from .demo import router as demo_router
from .dev import router as dev_router
from .grants import router as grants_router
from .health import router as health_router
from .retrievals import router as retrievals_router
from .uses import router as uses_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(grants_router)
api_router.include_router(retrievals_router)
api_router.include_router(copies_router)
api_router.include_router(assets_router)
api_router.include_router(uses_router)

if get_settings().enable_dev_endpoints:
    api_router.include_router(dev_router)
    api_router.include_router(demo_router)
