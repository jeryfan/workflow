from api.core.graphon.entities.base_node_data import BaseNodeData
from api.core.graphon.enums import BuiltinNodeTypes, NodeType
from api.core.graphon.nodes.base.entities import VariableSelector


class TemplateTransformNodeData(BaseNodeData):
    """Template Transform Node Data."""

    type: NodeType = BuiltinNodeTypes.TEMPLATE_TRANSFORM
    variables: list[VariableSelector]
    template: str
