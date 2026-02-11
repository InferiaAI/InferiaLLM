from fastapi import APIRouter

from inferia.services.filtration.management.organizations import router as organizations_router
from inferia.services.filtration.management.users import router as users_router
from inferia.services.filtration.management.deployments import router as deployments_router
from inferia.services.filtration.management.api_keys import router as api_keys_router
from inferia.services.filtration.management.configuration import router as config_router
from inferia.services.filtration.management.knowledge_base import router as kb_router
from inferia.services.filtration.management.prompts import router as prompts_router
from inferia.services.filtration.management.insights import router as insights_router

router = APIRouter(prefix="/management")

router.include_router(organizations_router)
router.include_router(users_router)
router.include_router(deployments_router)
router.include_router(api_keys_router)
router.include_router(config_router)
router.include_router(kb_router)
router.include_router(prompts_router)
router.include_router(insights_router)
