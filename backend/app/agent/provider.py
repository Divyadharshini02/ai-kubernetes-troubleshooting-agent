"""The interface every LLM provider implements, so the backend is not tied to one."""

from typing import Protocol


class ProviderError(Exception):
    """A provider failure, described by a safe code. It never carries provider text."""

    def __init__(self, code: str, *, retryable: bool = False, stop_all: bool = False):
        super().__init__(code)
        self.code = code
        # Trying the same request again may succeed.
        self.retryable = retryable
        # The next request would fail the same way, so no further calls should be made.
        self.stop_all = stop_all


class LlmProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, schema: dict, deadline: float) -> str:
        """Return the model's raw text. Raises ProviderError on any failure.

        There is deliberately no way to offer the model tools or functions.
        """
        ...
