"""Small capability contract; provider transports remain dataset-specific."""
from dataclasses import dataclass, field
from typing import Any, Protocol

CAPABILITIES = {'sentry': ('errors', 'logs', 'spans'), 'datadog': ('logs', 'spans', 'metrics', 'monitors')}


class ProviderError(Exception):
    def __init__(self, message, status=None, retry_after=0):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


@dataclass
class ProviderPage:
    records: list[dict[str, Any]]
    cursor: str | None = None
    warnings: list[str] = field(default_factory=list)


class ObservabilityProvider(Protocol):
    async def validate_connection(self, connection: dict) -> dict: ...
    async def fetch_page(self, connection: dict, dataset: str, options: dict, cursor: str | None = None) -> ProviderPage: ...
