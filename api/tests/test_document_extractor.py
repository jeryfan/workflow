import json
from pathlib import Path

from auth_helpers import authenticated_client
from docx import Document
from test_files import input_file, upload


def create_document_workflow(client) -> str:
    app = client.post(
        "/console/api/apps",
        json={"name": "Document Extraction", "mode": "workflow", "description": ""},
    ).json()
    graph = {
        "nodes": [
            {
                "id": "start",
                "type": "custom",
                "position": {"x": 80, "y": 160},
                "data": {
                    "title": "Start",
                    "type": "start",
                    "variables": [
                        {
                            "variable": "document",
                            "label": "Document",
                            "type": "file",
                            "required": True,
                            "options": [],
                        }
                    ],
                },
            },
            {
                "id": "extract",
                "type": "custom",
                "position": {"x": 360, "y": 160},
                "data": {
                    "title": "Extract",
                    "type": "document-extractor",
                    "variable_selector": ["start", "document"],
                    "is_array_file": False,
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 640, "y": 160},
                "data": {
                    "title": "End",
                    "type": "end",
                    "outputs": [{"variable": "text", "value_selector": ["extract", "text"]}],
                },
            },
        ],
        "edges": [
            {
                "id": "start-extract",
                "source": "start",
                "target": "extract",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "start", "targetType": "document-extractor"},
            },
            {
                "id": "extract-end",
                "source": "extract",
                "target": "end",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "document-extractor", "targetType": "end"},
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    saved = client.post(
        f"/console/api/apps/{app['id']}/workflows/draft",
        json={"graph": graph, "features": {}, "conversation_variables": [], "hash": None},
    )
    assert saved.status_code == 200, saved.text
    return app["id"]


def run_document(client, app_id: str, uploaded: dict) -> str:
    response = client.post(
        f"/console/api/apps/{app_id}/workflows/draft/run",
        json={"inputs": {"document": input_file(uploaded)}, "files": []},
    )
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["event"] == "workflow_finished"
    assert events[-1]["data"]["status"] == "succeeded", events[-1]
    return events[-1]["data"]["outputs"]["text"]


def test_extracts_plain_text_through_the_real_workflow(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        supported = client.get("/console/api/files/support-type")
        assert supported.status_code == 200
        assert {"pdf", "docx", "txt"}.issubset(supported.json()["allowed_extensions"])
        app_id = create_document_workflow(client)
        uploaded = upload(client, "document.txt", b"Hello document")
        assert run_document(client, app_id, uploaded) == "Hello document"


def test_extracts_docx_through_the_real_workflow(tmp_path: Path) -> None:
    document_path = tmp_path / "document.docx"
    document = Document()
    document.add_paragraph("Hello DOCX")
    document.save(document_path)
    with authenticated_client(tmp_path) as client:
        app_id = create_document_workflow(client)
        uploaded = upload(client, "document.docx", document_path.read_bytes())
        assert run_document(client, app_id, uploaded) == "Hello DOCX"
