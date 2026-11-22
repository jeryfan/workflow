from typing import override

from graphon.enums import NodeExecutionType, WorkflowNodeExecutionStatus
from graphon.node_events import NodeRunResult
from graphon.nodes.base.node import Node

from .entities import TriggerScheduleNodeData


class TriggerScheduleNode(Node[TriggerScheduleNodeData]):
    node_type = "trigger-schedule"
    execution_type = NodeExecutionType.ROOT

    @classmethod
    @override
    def version(cls) -> str:
        return "1"

    @override
    def _run(self) -> NodeRunResult:
        inputs = dict(self.runtime_state.variable_pool.get_by_prefix(self.id))
        return NodeRunResult(
            status=WorkflowNodeExecutionStatus.SUCCEEDED,
            inputs=inputs,
            outputs=dict(inputs),
        )
