from typing import Any

from pydantic import Field

from api.core.graphon.entities.base_node_data import BaseNodeData
from api.core.graphon.enums import NodeType


class TriggerScheduleNodeData(BaseNodeData):
    type: NodeType = "trigger-schedule"
    mode: str = Field(default="visual")
    frequency: str | None = None
    cron_expression: str | None = None
    visual_config: dict[str, Any] | None = None
    timezone: str = "UTC"
