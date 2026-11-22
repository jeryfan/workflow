import base64
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app

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
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    return client


def authenticated_database(database_path: Path) -> TestClient:
    client = TestClient(create_app(database_url=f"sqlite:///{database_path}"))
    if client.get("/console/api/setup").json()["step"] == "not_started":
        response = client.post("/console/api/setup", json=ADMIN)
        assert response.status_code == 201, response.text
    return login(client)


def authenticated_client(tmp_path: Path) -> TestClient:
    return authenticated_database(tmp_path / "workflow.db")
