"""Compatibility import for deployments using devpilot.api.app:create_app."""

from devpilot.api.main import create_app

__all__ = ["create_app"]
