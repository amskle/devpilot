"""Stable public facade for task runtime, commands, recovery, and queries."""

from devpilot.services.task_commands import TaskCommands
from devpilot.services.task_queries import TaskQueries
from devpilot.services.task_recovery import TaskRecoveryCommands
from devpilot.services.task_runtime import TaskRuntimeCore


class TaskService(
    TaskCommands,
    TaskRecoveryCommands,
    TaskQueries,
    TaskRuntimeCore,
):
    """Compose DevPilot task capabilities behind the historical public API."""
