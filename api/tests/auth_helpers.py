import asyncio
import base64
from pathlib import Path

from fastapi.testclient import TestClient
from service_helpers import ensure_app

from api.app import DEFAULT_APP_ID, create_app
from api.configs import app_config

ADMIN = {
    "email": "admin@example.com",
    "name": "Admin",
    "password": "Password123",
    "language": "en-US",
}


def encoded_password(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def login(client: TestClient, email: str = ADMIN["email"], password: str = ADMIN["password"]) -> TestClient:
    response = client.post(
        "/console/api/login",
        json={"email": email, "password": encoded_password(password)},
    )
    assert response.status_code == 200, response.text
    csrf_token = client.cookies.get("csrf_token") or client.cookies.get("__Host-csrf_token")
    assert csrf_token
    client.headers["X-CSRF-Token"] = csrf_token
    return client


def authenticated_database(database_path: Path) -> TestClient:
    app = create_app(database_url=f"sqlite:///{database_path}")

    async def initialize_default_app() -> None:
        async with app.state.session_factory() as db:
            await ensure_app(db, DEFAULT_APP_ID, name="Workflow")

    asyncio.run(initialize_default_app())
    secure = app_config.PUBLIC_API_URL.startswith("https://") and app_config.PUBLIC_WEB_URL.startswith("https://")
    client = TestClient(app, base_url="https://testserver" if secure else "http://testserver")
    if client.get("/console/api/setup").json()["step"] == "not_started":
        response = client.post("/console/api/setup", json=ADMIN)
        assert response.status_code == 201, response.text
    return login(client)


def authenticated_client(tmp_path: Path) -> TestClient:
    return authenticated_database(tmp_path / "workflow.db")
