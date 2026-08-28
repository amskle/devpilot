from fastapi import APIRouter

from devpilot.api.schemas import HealthResponse


router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check API health",
    operation_id="getApiHealth",
)
async def health() -> HealthResponse:
    return HealthResponse()
