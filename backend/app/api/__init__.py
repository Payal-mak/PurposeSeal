from fastapi import APIRouter

from .grants import router as grants_router
from .health import router as health_router
from .retrievals import router as retrievals_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(grants_router)
api_router.include_router(retrievals_router)
