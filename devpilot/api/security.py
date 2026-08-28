"""Compatibility exports for the original Phase 4 security module."""

from devpilot.api.core.config import ApiSettings, Principal
from devpilot.api.core.dependencies import authenticate
from devpilot.api.core.security import EventTicketStore, RateLimiter, bearer_scheme

__all__ = [
    "ApiSettings",
    "EventTicketStore",
    "Principal",
    "RateLimiter",
    "authenticate",
    "bearer_scheme",
]
