import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.libs.log_context import trace_id_var


class TraceMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = uuid.uuid4().hex
        scope.setdefault("state", {})["trace_id"] = trace_id
        token = trace_id_var.set(trace_id)

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            trace_id_var.reset(token)
