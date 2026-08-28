from __future__ import annotations

from pydantic import Field

from devpilot.api.schemas.common import ApiModel


class ControlTargetRequest(ApiModel):
    expected_state_revision: int = Field(
        ge=0,
        description="Revision last observed by the caller; stale revisions return HTTP 409",
        examples=[12],
    )


class ApprovalDecisionRequest(ControlTargetRequest):
    approval_id: str = Field(examples=["approval_4f279acbcdab4d6a"])
    patch_hash: str = Field(min_length=64, max_length=64, examples=["a" * 64])
    base_revision: str = Field(examples=["58b087af6b48"])


class RecoveryControlRequest(ControlTargetRequest):
    recovery_point_id: str = Field(examples=["recovery_cdfda6e4f34041a8"])


class ChangeRequestBody(ControlTargetRequest):
    content: str = Field(
        min_length=1,
        max_length=20_000,
        description="Replacement or additional requirement that requires a new Plan version",
        examples=["保留旧配置格式，并为迁移路径增加回归测试。"],
    )
    confirm_patch_invalidation: bool = Field(
        description="Must be true when a risk approval is pending"
    )


class MessageCreateRequest(ApiModel):
    content: str = Field(
        min_length=1,
        max_length=20_000,
        description="Conversation text only; it never executes a control operation",
        examples=["这个错误最早出现在哪个验证阶段？"],
    )
