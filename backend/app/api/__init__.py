from fastapi import APIRouter

from .assets import router as assets_router
from .copies import router as copies_router
from .grants import router as grants_router
from .health import router as health_router
from .retrievals import router as retrievals_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(grants_router)
api_router.include_router(retrievals_router)
api_router.include_router(copies_router)
api_router.include_router(assets_router)
