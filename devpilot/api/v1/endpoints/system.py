import asyncio

from fastapi import APIRouter, Request, Response, status

from devpilot.api.schemas import HealthResponse, ReadinessResponse


router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check API health",
    operation_id="getApiHealth",
)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Check required API dependencies",
    operation_id="getApiReadiness",
)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    redis_client = request.app.state.redis_client
    redis_status = "disabled"
    if redis_client is not None:
        try:
            await asyncio.to_thread(redis_client.ping)
            redis_status = "ok"
        except Exception:
            redis_status = "unavailable"
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    ready = redis_status != "unavailable"
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        dependencies={"redis": redis_status},
    )
