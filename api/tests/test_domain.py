import json
from pathlib import Path

import pytest

from api.models.entities import GraphDocument, generate_draft_hash

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/reference-workflow.json").read_text())
EXPECTED_HASH = json.loads((ROOT / "contracts/fixtures/reference-hash.json").read_text())["expected_hash"]


def test_graph_document_preserves_the_dify_graph_and_hash() -> None:
    document = GraphDocument.parse(FIXTURE["graph"])

    assert document.to_json_object() == FIXTURE["graph"]
    assert set(document.nodes_by_id) == {"start", "template", "end"}
    assert generate_draft_hash(document) == EXPECTED_HASH


def test_graph_document_does_not_share_mutable_input_or_output() -> None:
    source = deep_copy_graph()
    document = GraphDocument.parse(source)
    source["nodes"][0]["data"]["title"] = "Changed outside"

    exported = document.to_json_object()
    exported["nodes"][0]["data"]["title"] = "Changed export"

    assert document.nodes_by_id["start"]["data"]["title"] == "Start"
    with pytest.raises(TypeError):
        document.nodes_by_id["start"]["data"]["title"] = "Changed directly"


def test_graph_document_rejects_duplicate_node_ids() -> None:
    graph = deep_copy_graph()
    graph["nodes"][1]["id"] = "start"

    with pytest.raises(ValueError, match="Duplicate node id: start"):
        GraphDocument.parse(graph)


def deep_copy_graph() -> dict:
    return json.loads(json.dumps(FIXTURE["graph"]))
