#!/usr/bin/env python3

import base64
import json
import os
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
BASE_URL = os.environ.get("WORKFLOW_API_URL", "http://127.0.0.1:8000").rstrip("/")


class LiveApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.csrf_token: str | None = None

    def _open(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.csrf_token is not None:
            headers["X-CSRF-Token"] = self.csrf_token
        request = Request(f"{self.base_url}{path}", data=data, method=method, headers=headers)
        return self.opener.open(request, timeout=30)

    def json(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            with self._open(method, path, body) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404:
                return json.load(error)
            raise

    def login(self, email: str, password: str) -> None:
        encoded_password = base64.b64encode(password.encode()).decode()
        self.json("POST", "/console/api/login", {"email": email, "password": encoded_password})
        self.csrf_token = next(
            (cookie.value for cookie in self.cookies if cookie.name in {"csrf_token", "__Host-csrf_token"}),
            None,
        )
        if self.csrf_token is None:
            raise RuntimeError("Login did not issue a CSRF cookie")

    def verify_reference(self, fixture: dict) -> list[dict]:
        app_path = f"/console/api/apps/{fixture['app_id']}"
        draft_path = f"{app_path}/workflows/draft"
        current = self.json("GET", draft_path)
        saved = self.json(
            "POST",
            draft_path,
            {
                "graph": fixture["graph"],
                "features": {},
                "conversation_variables": [],
                "hash": current.get("hash"),
            },
        )
        assert saved["result"] == "success"

        with self._open(
            "POST",
            f"{draft_path}/run",
            {"inputs": fixture["inputs"], "files": []},
        ) as response:
            assert response.headers.get_content_type() == "text/event-stream"
            events = [
                json.loads(line.decode()[6:])
                for line in response
                if line.decode().startswith("data: ")
            ]

        assert [event["event"] for event in events] == fixture["expected_events"]
        assert events[-1]["data"]["status"] == "succeeded"
        assert events[-1]["data"]["outputs"] == fixture["expected_outputs"]
        return events


def main() -> None:
    email = os.environ.get("WORKFLOW_ADMIN_EMAIL")
    password = os.environ.get("WORKFLOW_ADMIN_PASSWORD")
    if not email or not password:
        raise SystemExit("Set WORKFLOW_ADMIN_EMAIL and WORKFLOW_ADMIN_PASSWORD to an account with workflow edit access")

    client = LiveApiClient(BASE_URL)
    client.login(email, password)
    events = client.verify_reference(FIXTURE)
    print(json.dumps({"events": len(events), "outputs": events[-1]["data"]["outputs"]}))


if __name__ == "__main__":
    main()
