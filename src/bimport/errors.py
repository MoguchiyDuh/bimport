from typing import Any


class BimError(Exception):
    code = 1

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


class UsageError(BimError):
    code = 2


class NotFound(BimError):
    code = 3


class Ambiguous(BimError):
    code = 4


class Duplicate(BimError):
    code = 5


class SyncError(BimError):
    code = 6
