import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from test_containers import run_graph, terminal_outputs


def test_code_node_uses_the_sandbox_and_returns_declared_output(tmp_path: Path, monkeypatch) -> None:
    requests = []

    class SandboxHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            response = json.dumps(
                {
                    "code": 0,
                    "data": {"stdout": '<<RESULT>>{"result": "Hello"}\n<<RESULT>>', "error": ""},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SandboxHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("CODE_EXECUTION_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    graph = {
        "nodes": [
            {"id": "start", "data": {"type": "start", "title": "Start", "variables": []}},
            {
                "id": "code",
                "data": {
                    "type": "code",
                    "title": "Code",
                    "variables": [{"variable": "arg1", "value_selector": ["start", "text"]}],
                    "code_language": "python3",
                    "code": "def main(arg1: str):\n    return {'result': arg1}",
                    "outputs": {"result": {"type": "string", "children": None}},
                },
            },
            {
                "id": "end",
                "data": {
                    "type": "end",
                    "title": "End",
                    "outputs": [{"variable": "message", "value_selector": ["code", "result"]}],
                },
            },
        ],
        "edges": [{"source": "start", "target": "code"}, {"source": "code", "target": "end"}],
    }
    try:
        events = run_graph(graph, {"text": "Hello"}, tmp_path / "code.db")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert terminal_outputs(events) == {"message": "Hello"}
    assert requests[0]["language"] == "python3"
    assert "def main(arg1: str):" in requests[0]["code"]
