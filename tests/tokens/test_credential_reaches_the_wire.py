"""The credential's headers reach the actual HTTP request.

Every layer in between can be unit-tested and still leave the cookie stranded:
ModelSettings -> LitellmModel -> litellm -> provider transformation -> httpx. A
gateway that authenticates on a session cookie fails CLOSED and looks exactly like
a bad password, so this drives the real runner against a local HTTP server and
reads the headers off the wire.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.resources import AgentConfig
from workspace_app.tokens import CallLane, ITokenService, LlmCredential
from workspace_app.users.protocol import User

_SSE = (
    'data: {"id":"1","object":"chat.completion.chunk","created":0,"model":"m",'
    '"choices":[{"index":0,"delta":{"role":"assistant","content":"hi"},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"1","object":"chat.completion.chunk","created":0,"model":"m",'
    '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


class _CapturingGateway:
    """A stand-in for the LLM gateway: records the headers of every request."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []
        seen = self.headers

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                seen.append({k.lower(): v for k, v in self.headers.items()})
                body = _SSE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass  # keep pytest output clean

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> _CapturingGateway:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _CookieService(ITokenService):
    """A gateway that authenticates on a session cookie and wants the lane tagged."""

    async def get_credential(
        self, user_id: str, current_key: str | None, lane: CallLane
    ) -> LlmCredential:
        return LlmCredential(current_key, {"Cookie": f"session={user_id}", "X-Lane": lane})


async def test_the_credentials_cookie_and_lane_arrive_as_http_headers():
    with _CapturingGateway() as gateway:
        runner = LitellmAgentRunner(token_service=_CookieService(), max_turns=1)
        ctx = AgentToolContext(
            speaker=User(id="alice", name="A"),
            call_lane="interactive",
            agent_config=AgentConfig(
                name="p",
                model="openai/test-model",
                llm_base_url=gateway.url,
                llm_api_key="sk-test",
                allowed_tools=[],
            ),
        )
        async for _ev in runner.run("hi", ctx):
            pass

        assert gateway.headers, "the runner never reached the gateway"
        sent = gateway.headers[0]
        assert sent["cookie"] == "session=alice"
        assert sent["x-lane"] == "interactive"
        # the api_key still authenticates the usual way alongside it
        assert sent["authorization"] == "Bearer sk-test"


async def test_a_deploy_with_no_credential_source_sends_no_extra_headers():
    # the passthrough default must stay byte-for-byte what it sent before
    with _CapturingGateway() as gateway:
        runner = LitellmAgentRunner(max_turns=1)  # token_service=None
        ctx = AgentToolContext(
            agent_config=AgentConfig(
                name="p",
                model="openai/test-model",
                llm_base_url=gateway.url,
                llm_api_key="sk-test",
                allowed_tools=[],
            ),
        )
        async for _ev in runner.run("hi", ctx):
            pass

        assert gateway.headers
        assert "cookie" not in gateway.headers[0]
        assert "x-lane" not in gateway.headers[0]
