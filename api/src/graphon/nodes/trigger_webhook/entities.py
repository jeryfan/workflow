from collections.abc import Sequence

from pydantic import BaseModel, Field, field_validator

from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType
from graphon.variables.types import SegmentType


class WebhookParameter(BaseModel):
    name: str
    type: SegmentType = SegmentType.STRING
    required: bool = False


class WebhookData(BaseNodeData):
    type: NodeType = "trigger-webhook"
    webhook_url: str | None = None
    webhook_debug_url: str | None = None
    method: str = "POST"
    content_type: str = "application/json"
    headers: Sequence[WebhookParameter] = Field(default_factory=list)
    params: Sequence[WebhookParameter] = Field(default_factory=list)
    body: Sequence[WebhookParameter] = Field(default_factory=list)
    async_mode: bool = True
    status_code: int = 200
    response_body: str = ""
    timeout: int = 30

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value):
        return value.lower() if isinstance(value, str) else value
