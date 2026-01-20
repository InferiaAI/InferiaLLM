from fastapi import APIRouter

from management.organizations import router as organizations_router
from management.users import router as users_router
from management.deployments import router as deployments_router
from management.api_keys import router as api_keys_router
from management.config import router as config_router
from management.knowledge_base import router as kb_router
from management.prompts import router as prompts_router

router = APIRouter(prefix="/management")

router.include_router(organizations_router)
router.include_router(users_router)
router.include_router(deployments_router)
router.include_router(api_keys_router)
router.include_router(config_router)
router.include_router(kb_router)
router.include_router(prompts_router)
