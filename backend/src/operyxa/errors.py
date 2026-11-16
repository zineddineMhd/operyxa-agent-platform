"""Deterministic error codes shared by the API, the gateway and the agents."""


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class Unauthorized(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"


class BudgetExceeded(Exception):
    """A per-run limit was hit. `kind` is one of: llm_calls, tool_calls, time, cost."""

    def __init__(self, kind: str):
        super().__init__(f"run budget exceeded: {kind}")
        self.kind = kind


class RunCancelled(Exception):
    pass
