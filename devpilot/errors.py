class DevPilotError(RuntimeError):
    code = "DEVPILOT_ERROR"


class StateConflictError(DevPilotError):
    code = "STATE_CONFLICT"


class PolicyDeniedError(DevPilotError):
    code = "POLICY_DENY"


class BudgetExceededError(DevPilotError):
    code = "BUDGET_EXHAUSTED"


class ToolExecutionError(DevPilotError):
    def __init__(self, code: str, message: str, *, transient: bool = False):
        super().__init__(message)
        self.code = code
        self.transient = transient


class ModelGatewayError(DevPilotError):
    code = "MODEL_GATEWAY_ERROR"
