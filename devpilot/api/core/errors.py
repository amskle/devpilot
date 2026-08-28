from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from devpilot.api.core.security import SharedStateUnavailableError
from devpilot.api.schemas import ProblemDetails
from devpilot.errors import BudgetExceededError, PolicyDeniedError, StateConflictError


def _problem(
    request: Request,
    status_code: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ProblemDetails(
            code=code,
            detail=detail,
            request_id=getattr(request.state, "request_id", None),
        ).model_dump(mode="json"),
        headers=headers,
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid request"}
        return _problem(request, 422, "REQUEST_VALIDATION_FAILED", str(first["msg"]))

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        codes = {
            401: "AUTHENTICATION_REQUIRED",
            403: "FORBIDDEN",
            404: "RESOURCE_NOT_FOUND",
            429: "RATE_LIMIT_EXCEEDED",
        }
        return _problem(
            request,
            exc.status_code,
            codes.get(exc.status_code, "HTTP_ERROR"),
            str(exc.detail),
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(StateConflictError)
    async def state_conflict_handler(
        request: Request, exc: StateConflictError
    ) -> JSONResponse:
        return _problem(request, 409, "STATE_CONFLICT", str(exc))

    @app.exception_handler(BudgetExceededError)
    async def budget_handler(
        request: Request, exc: BudgetExceededError
    ) -> JSONResponse:
        return _problem(request, 409, "BUDGET_EXHAUSTED", str(exc))

    @app.exception_handler(PolicyDeniedError)
    async def policy_handler(
        request: Request, exc: PolicyDeniedError
    ) -> JSONResponse:
        return _problem(request, 403, "POLICY_DENIED", str(exc))

    @app.exception_handler(KeyError)
    async def not_found_handler(request: Request, _: KeyError) -> JSONResponse:
        return _problem(request, 404, "RESOURCE_NOT_FOUND", "resource not found")

    @app.exception_handler(ValueError)
    async def value_handler(request: Request, exc: ValueError) -> JSONResponse:
        return _problem(request, 422, "DOMAIN_VALIDATION_FAILED", str(exc))

    @app.exception_handler(SharedStateUnavailableError)
    async def shared_state_handler(
        request: Request, _: SharedStateUnavailableError
    ) -> JSONResponse:
        return _problem(
            request,
            503,
            "SHARED_STATE_UNAVAILABLE",
            "distributed security state is temporarily unavailable",
            headers={"Retry-After": "1"},
        )
