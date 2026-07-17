import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from teampulse.models import (
    BriefRevisionStatus,
    ClaimStatus,
    Provider,
    SourceItemKind,
)


class WorkspaceCreate(BaseModel):
    name: str
    timezone: str = "Asia/Seoul"


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    timezone: str
    created_at: datetime


class ProjectCreate(BaseModel):
    workspace_id: uuid.UUID
    name: str
    description: str | None = None
    daily_report_channel_id: str | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str | None
    daily_report_channel_id: str | None
    active: bool
    created_at: datetime


class ProjectMemberCreate(BaseModel):
    display_name: str
    email: str
    role: str = "member"


class ProjectMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    display_name: str
    email: str
    role: str
    active: bool
    created_at: datetime


class IntegrationCreate(BaseModel):
    provider: Provider
    external_id: str
    name: str
    credentials: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    provider: Provider
    external_id: str
    name: str
    config: dict[str, Any]
    status: str
    created_at: datetime


class IntegrationPollRead(BaseModel):
    integration_id: uuid.UUID
    channel_id: str
    fetched: int
    stored: int
    duplicates: int
    last_message_id: str | None


class SourceItemCreate(BaseModel):
    project_id: uuid.UUID
    integration_id: uuid.UUID | None = None
    provider: Provider
    external_id: str
    kind: SourceItemKind = SourceItemKind.UNKNOWN
    title: str
    body: str = ""
    source_url: HttpUrl | str | None = None
    occurred_at: datetime
    actor: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class SourceItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    integration_id: uuid.UUID | None
    provider: Provider
    external_id: str
    kind: SourceItemKind
    title: str
    body: str
    source_url: str | None
    occurred_at: datetime
    actor: dict[str, Any]
    metadata: dict[str, Any] = Field(validation_alias="source_metadata")


class BriefClaim(BaseModel):
    text: str
    status: ClaimStatus
    source_item_ids: list[str] = Field(default_factory=list)


class BriefSection(BaseModel):
    key: Literal[
        "direction",
        "design_changes",
        "decisions",
        "planning",
        "tasks",
        "completed",
        "schedule_risks",
        "conflicts",
    ]
    title: str
    claims: list[BriefClaim] = Field(default_factory=list)


class BriefContent(BaseModel):
    sections: list[BriefSection]
    source_window: dict[str, Any] = Field(default_factory=dict)
    diff_from_last_confirmed: list[str] = Field(default_factory=list)


class BriefRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    title: str
    revision_hash: str
    status: BriefRevisionStatus
    content: dict[str, Any]
    approver_snapshot: list[dict[str, Any]]
    source_item_ids: list[str]
    created_by: str
    created_at: datetime
    confirmed_at: datetime | None


class BriefGenerateRequest(BaseModel):
    since: datetime | None = None
    until: datetime | None = None


class BriefEditRequest(BaseModel):
    content: BriefContent
    created_by: str = "user"


class ApprovalRead(BaseModel):
    revision_id: uuid.UUID
    revision_hash: str
    required_count: int
    approved_count: int
    pending_member_ids: list[str]
    status: BriefRevisionStatus


class DiscordNotificationRead(BaseModel):
    brief_revision_id: uuid.UUID
    channel_id: str
    delivered: bool
    duplicate: bool
    external_message_id: str | None = None
