from collections.abc import Sequence

from pydantic import Field

from api.core.graphon.entities.base_node_data import BaseNodeData
from api.core.graphon.enums import BuiltinNodeTypes, NodeType
from api.core.graphon.variables.input_entities import VariableEntity


class StartNodeData(BaseNodeData):
    """Start Node Data"""

    type: NodeType = BuiltinNodeTypes.START
    variables: Sequence[VariableEntity] = Field(default_factory=list)
