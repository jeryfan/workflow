from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from auth_helpers import authenticated_client
from test_containers import run_graph, terminal_outputs


def test_http_node_uses_the_proxy_and_templates_request_values(tmp_path: Path, monkeypatch) -> None:
    requests = []

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append({"path": self.path, "test": self.headers.get("X-Test")})
            response = b"proxied response"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_REQUEST_PROXY", f"http://127.0.0.1:{server.server_port}")
    graph = {
        "nodes": [
            {"id": "start", "data": {"type": "start", "title": "Start", "variables": []}},
            {
                "id": "request",
                "data": {
                    "type": "http-request",
                    "title": "Request",
                    "method": "get",
                    "url": "http://workflow.example/{{#start.path#}}",
                    "authorization": {"type": "no-auth"},
                    "headers": "X-Test: {{#start.text#}}",
                    "params": "",
                    "body": {"type": "none"},
                    "ssl_verify": True,
                },
            },
            {
                "id": "end",
                "data": {
                    "type": "end",
                    "title": "End",
                    "outputs": [{"variable": "body", "value_selector": ["request", "body"]}],
                },
            },
        ],
        "edges": [{"source": "start", "target": "request"}, {"source": "request", "target": "end"}],
    }
    try:
        events = run_graph(graph, {"path": "status", "text": "templated"}, tmp_path / "http.db")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert terminal_outputs(events) == {"body": "proxied response"}
    assert requests == [{"path": "http://workflow.example/status", "test": "templated"}]


def test_http_default_config_contains_empty_body_data_for_the_original_editor(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        response = client.get(
            "/console/api/apps/00000000-0000-0000-0000-000000000001/workflows/default-workflow-block-configs/http-request"
        )

    assert response.status_code == 200
    assert response.json()["body"] == {"type": "none", "data": []}
