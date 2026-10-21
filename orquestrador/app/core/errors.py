from __future__ import annotations


class CoreError(RuntimeError):
    """Base error for core API calls.

    `transient` marks errors worth retrying (timeouts, connection failures and
    5xx). Definitive errors (4xx other than the modeled business outcomes) are
    not retried, per the item-by-item recording requirement.
    """

    transient: bool = False

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class CoreTransientError(CoreError):
    transient = True


class CoreTimeoutError(CoreTransientError):
    def __init__(self) -> None:
        super().__init__("core request timed out")


class CoreServerError(CoreTransientError):
    def __init__(self, status: int) -> None:
        super().__init__("core server error", status)


class CoreValidationError(CoreError):
    """422: the request was rejected by validation; never retried."""

    def __init__(self) -> None:
        super().__init__("core rejected the request (validation)", 422)


class CoreConflictError(CoreError):
    """409: same idempotency key with a different payload; never retried."""

    def __init__(self) -> None:
        super().__init__("core reported a conflict", 409)


class CoreNotFoundError(CoreError):
    """404: unknown or inactive supply; never retried automatically."""

    def __init__(self) -> None:
        super().__init__("core resource not found", 404)


class CoreClientError(CoreError):
    """Other 4xx: definitive."""

    def __init__(self, status: int) -> None:
        super().__init__("core client error", status)
