class HarnessError(Exception):
    """Base runtime error."""


class PermissionDenied(HarnessError):
    """Raised when a tool action is blocked by permission policy."""

