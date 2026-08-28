from fastapi import APIRouter

from devpilot.api.v1.endpoints import conversation, controls, events, evidence, system, tasks


router = APIRouter()
router.include_router(system.router)
router.include_router(tasks.router)
router.include_router(evidence.router)
router.include_router(conversation.router)
router.include_router(events.router)
router.include_router(controls.router)

__all__ = ["router"]
