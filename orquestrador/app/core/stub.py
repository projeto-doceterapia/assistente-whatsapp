from __future__ import annotations

from collections.abc import Callable

import httpx

Handler = Callable[[httpx.Request], httpx.Response]


class CoreStub:
    """Programmable core backend for tests, served through httpx.MockTransport.

    Tests register a handler per (method, path) and get an AsyncClient wired to
    this stub, so the real CoreClient runs its full status-code mapping against
    canned responses. A handler may raise httpx.TimeoutException to exercise the
    transient path.
    """

    def __init__(self, base_url: str = "http://core.test") -> None:
        self._handlers: dict[tuple[str, str], Handler] = {}
        self.base_url = base_url
        self.requests: list[httpx.Request] = []

    def on(self, method: str, path: str, handler: Handler) -> None:
        self._handlers[(method.upper(), path)] = handler

    def json(self, method: str, path: str, status: int, body: object) -> None:
        self.on(method, path, lambda _request: httpx.Response(status, json=body))

    def status(self, method: str, path: str, status: int) -> None:
        self.on(method, path, lambda _request: httpx.Response(status))

    def timeout(self, method: str, path: str) -> None:
        def _raise(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("stubbed timeout", request=request)

        self.on(method, path, _raise)

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self._handlers.get((request.method, request.url.path))
        if handler is None:
            return httpx.Response(501, json={"detail": "no stub for this route"})
        return handler(request)

    def client(self) -> httpx.AsyncClient:
        transport = httpx.MockTransport(self._dispatch)
        return httpx.AsyncClient(transport=transport, base_url=self.base_url)
