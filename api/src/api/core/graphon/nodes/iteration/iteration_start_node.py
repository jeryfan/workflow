from typing import override

from api.core.graphon.enums import BuiltinNodeTypes, WorkflowNodeExecutionStatus
from api.core.graphon.node_events.base import NodeRunResult
from api.core.graphon.nodes.base.node import Node
from api.core.graphon.nodes.iteration.entities import IterationStartNodeData


class IterationStartNode(Node[IterationStartNodeData]):
    """Iteration Start Node."""

    node_type = BuiltinNodeTypes.ITERATION_START

    @classmethod
    @override
    def version(cls) -> str:
        return "1"

    @override
    def _run(self) -> NodeRunResult:
        """Run the node."""
        return NodeRunResult(status=WorkflowNodeExecutionStatus.SUCCEEDED)
