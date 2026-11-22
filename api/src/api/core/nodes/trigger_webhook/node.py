from typing import override

from api.core.graphon.enums import NodeExecutionType, WorkflowNodeExecutionStatus
from api.core.graphon.node_events import NodeRunResult
from api.core.graphon.nodes.base.node import Node

from .entities import WebhookData


class TriggerWebhookNode(Node[WebhookData]):
    node_type = "trigger-webhook"
    execution_type = NodeExecutionType.ROOT

    @classmethod
    @override
    def version(cls) -> str:
        return "1"

    @override
    def _run(self) -> NodeRunResult:
        inputs = dict(self.runtime_state.variable_pool.get_by_prefix(self.id))
        webhook_data = inputs.get("webhook_data", {})
        if not isinstance(webhook_data, dict):
            webhook_data = {}
        outputs = {}
        headers = webhook_data.get("headers", {})
        normalized_headers = (
            {str(key).lower(): value for key, value in headers.items()} if isinstance(headers, dict) else {}
        )
        query = webhook_data.get("query_params", {})
        body = webhook_data.get("body", {})
        for parameter in self.node_data.headers:
            outputs[parameter.name.replace("-", "_")] = normalized_headers.get(parameter.name.lower())
        for parameter in self.node_data.params:
            outputs[parameter.name] = query.get(parameter.name) if isinstance(query, dict) else None
        for parameter in self.node_data.body:
            if self.node_data.content_type == "text/plain" and isinstance(body, dict):
                outputs[parameter.name] = str(body.get("raw", ""))
            else:
                outputs[parameter.name] = body.get(parameter.name) if isinstance(body, dict) else None
        outputs["_webhook_raw"] = webhook_data
        return NodeRunResult(
            status=WorkflowNodeExecutionStatus.SUCCEEDED,
            inputs=inputs,
            outputs=outputs,
        )
