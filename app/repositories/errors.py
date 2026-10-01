class RepositoryError(Exception):
    """Base class for repository-level errors."""


class NotFoundError(RepositoryError):
    def __init__(self, model: str, key: object) -> None:
        super().__init__(f"{model} not found: {key!r}")
        self.model = model
        self.key = key


class InvalidTransitionError(RepositoryError, ValueError):
    """A crawl status change that is not allowed."""
