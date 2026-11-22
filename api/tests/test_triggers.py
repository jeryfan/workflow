import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from auth_helpers import authenticated_client


def graph(trigger: dict) -> dict:
    trigger_id = trigger["id"]
    trigger_type = trigger["data"]["type"]
    variable = "name" if trigger_type == "trigger-webhook" else "scheduled_at"
    return {
        "nodes": [
            trigger,
            {
                "id": "template",
                "type": "custom",
                "position": {"x": 400, "y": 160},
                "data": {
                    "title": "Template",
                    "type": "template-transform",
                    "variables": [{"variable": "value", "value_selector": [trigger_id, variable]}],
                    "template": "Received {{ value }}",
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 720, "y": 160},
                "data": {
                    "title": "End",
                    "type": "end",
                    "outputs": [{"variable": "message", "value_selector": ["template", "output"]}],
                },
            },
        ],
        "edges": [
            {
                "id": "trigger-template",
                "source": trigger_id,
                "target": "template",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": trigger_type, "targetType": "template-transform"},
            },
            {
                "id": "template-end",
                "source": "template",
                "target": "end",
                "sourceHandle": "source",
                "targetHandle": "target",
                "data": {"sourceType": "template-transform", "targetType": "end"},
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def create_trigger_app(client, trigger: dict) -> str:
    app = client.post(
        "/console/api/apps",
        json={"name": trigger["data"]["title"], "mode": "workflow", "description": ""},
    ).json()
    saved = client.post(
        f"/console/api/apps/{app['id']}/workflows/draft",
        json={"graph": graph(trigger), "features": {}, "conversation_variables": [], "hash": None},
    )
    assert saved.status_code == 200, saved.text
    return app["id"]


def publish(client, app_id: str) -> None:
    response = client.post(f"/console/api/apps/{app_id}/workflows/publish", json={})
    assert response.status_code == 200, response.text


def wait_for_run(client, app_id: str, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/console/api/apps/{app_id}/workflow-runs/{run_id}")
        if response.status_code == 200 and response.json()["status"] != "running":
            return response.json()
        time.sleep(0.02)
    raise AssertionError("Workflow run did not finish")


def test_webhook_generates_stable_url_and_executes_the_published_graph(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_trigger_app(
            client,
            {
                "id": "webhook",
                "type": "custom",
                "position": {"x": 80, "y": 160},
                "data": {
                    "title": "Webhook",
                    "type": "trigger-webhook",
                    "method": "POST",
                    "content_type": "application/json",
                    "headers": [],
                    "params": [],
                    "body": [{"name": "name", "type": "string", "required": True}],
                    "async_mode": True,
                    "status_code": 202,
                    "response_body": '{"accepted":true}',
                    "variables": [],
                },
            },
        )
        generated = client.get(
            f"/console/api/apps/{app_id}/workflows/triggers/webhook",
            params={"node_id": "webhook"},
        )
        assert generated.status_code == 200, generated.text
        webhook_id = generated.json()["webhook_id"]
        publish(client, app_id)
        listed = client.get(f"/console/api/apps/{app_id}/triggers").json()["data"]
        assert listed[0]["status"] == "enabled"
        assert listed[0]["node_id"] == "webhook"

        response = client.post(f"/triggers/webhook/{webhook_id}", json={"name": "World"})
        assert response.status_code == 202, response.text
        assert response.json() == {"accepted": True}
        run_id = response.headers["X-Workflow-Run-Id"]
        run = wait_for_run(client, app_id, run_id)
        assert run["status"] == "succeeded"
        assert run["outputs"] == {"message": "Received World"}

        disabled = client.post(
            f"/console/api/apps/{app_id}/trigger-enable",
            json={"trigger_id": listed[0]["id"], "enable_trigger": False},
        )
        assert disabled.status_code == 200
        assert client.post(f"/triggers/webhook/{webhook_id}", json={"name": "World"}).status_code == 404
        debug = client.post(f"/triggers/webhook-debug/{webhook_id}", json={"name": "Draft"})
        assert debug.status_code == 202, debug.text
        debug_run = wait_for_run(client, app_id, debug.headers["X-Workflow-Run-Id"])
        assert debug_run["outputs"] == {"message": "Received Draft"}


def test_webhook_rejects_wrong_method_and_missing_required_input(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_trigger_app(
            client,
            {
                "id": "webhook",
                "type": "custom",
                "position": {"x": 80, "y": 160},
                "data": {
                    "title": "Webhook",
                    "type": "trigger-webhook",
                    "method": "POST",
                    "content_type": "application/json",
                    "headers": [],
                    "params": [],
                    "body": [{"name": "name", "type": "string", "required": True}],
                },
            },
        )
        generated = client.get(
            f"/console/api/apps/{app_id}/workflows/triggers/webhook",
            params={"node_id": "webhook"},
        ).json()
        publish(client, app_id)
        webhook_id = generated["webhook_id"]
        wrong_method = client.get(f"/triggers/webhook/{webhook_id}")
        missing = client.post(f"/triggers/webhook/{webhook_id}", json={})
        malformed = client.post(
            f"/triggers/webhook/{webhook_id}",
            content="{broken",
            headers={"Content-Type": "application/json"},
        )

    assert wrong_method.status_code == 400
    assert missing.status_code == 400
    assert malformed.status_code == 400


def test_scheduler_dispatches_each_due_window_only_once(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_trigger_app(
            client,
            {
                "id": "schedule",
                "type": "custom",
                "position": {"x": 80, "y": 160},
                "data": {
                    "title": "Schedule",
                    "type": "trigger-schedule",
                    "mode": "cron",
                    "cron_expression": "* * * * *",
                    "timezone": "UTC",
                },
            },
        )
        publish(client, app_id)
        now = int(datetime.now(UTC).timestamp())
        with sqlite3.connect(tmp_path / "workflow.db") as connection:
            connection.execute(
                "UPDATE workflow_triggers SET next_run_at = ? WHERE app_id = ?",
                (now - 1, app_id),
            )
        scheduler = client.app.state.trigger_scheduler
        first = scheduler.poll_once(datetime.fromtimestamp(now, UTC))
        second = scheduler.poll_once(datetime.fromtimestamp(now, UTC))
        store = client.app.state.store
        for _ in range(100):
            page = store.list_runs(app_id, 10, None, None)
            if page.records and page.records[0].status != "running":
                break
            time.sleep(0.02)
        assert first == 1
        assert second == 0
        assert len(page.records) == 1
        assert page.records[0].status == "succeeded"
        assert page.records[0].outputs["message"].startswith("Received ")
        trigger = store.list_workflow_triggers(app_id)[0]
        assert trigger.last_run_key == str(now - 1)
        assert trigger.next_run_at > now - 1


def test_invalid_cron_is_rejected_before_publication(tmp_path: Path) -> None:
    with authenticated_client(tmp_path) as client:
        app_id = create_trigger_app(
            client,
            {
                "id": "schedule",
                "type": "custom",
                "position": {"x": 80, "y": 160},
                "data": {
                    "title": "Schedule",
                    "type": "trigger-schedule",
                    "mode": "cron",
                    "cron_expression": "not a cron",
                    "timezone": "UTC",
                },
            },
        )
        response = client.post(f"/console/api/apps/{app_id}/workflows/publish", json={})
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_trigger"
        assert client.get(f"/console/api/apps/{app_id}/triggers").json() == {"data": []}
