from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from app.llm.port import LlmRequest, LlmResponseError, Schema


class FakeLlmProvider:
    """LlmPort that replays recorded JSON responses, for tests.

    Seed it with the raw JSON strings a real provider would return (one per
    expected call). Each :meth:`generate` pops the next recording and validates
    it against the requested schema, exactly like the real provider, so flows
    run deterministically and never touch the network. Every request is kept in
    :attr:`requests` so tests can assert what the orchestrator sent (for example
    the rendered catalog or the search candidates).
    """

    def __init__(self, responses: Iterable[str] | None = None) -> None:
        self._responses: deque[str] = deque(responses or [])
        self.requests: list[LlmRequest] = []

    def queue(self, response_json: str) -> None:
        self._responses.append(response_json)

    async def generate(self, request: LlmRequest, schema: type[Schema]) -> Schema:
        self.requests.append(request)
        if not self._responses:
            raise LlmResponseError("fake provider has no more recorded responses")
        raw = self._responses.popleft()
        return schema.model_validate_json(raw)
