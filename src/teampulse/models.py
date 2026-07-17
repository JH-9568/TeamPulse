import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Provider(StrEnum):
    FIGMA = "figma"
    NOTION = "notion"
    DISCORD = "discord"
    GITHUB = "github"
    SLACK = "slack"


class IntegrationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class SourceItemKind(StrEnum):
    DESIGN_UPDATE = "design_update"
    DESIGN_COMMENT = "design_comment"
    PLANNING_DOC = "planning_doc"
    TASK_CHANGE = "task_change"
    MEETING_MESSAGE = "meeting_message"
    COMMAND = "command"
    UNKNOWN = "unknown"


class SourceItemStatus(StrEnum):
    RAW = "raw"
    NORMALIZED = "normalized"
    FAILED = "failed"


class ClaimStatus(StrEnum):
    CONFIRMED = "confirmed"
    AI_INFERENCE = "ai_inference"
    CONFLICT = "conflict"
    NEEDS_CONFIRMATION = "needs_confirmation"


class BriefRevisionStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Seoul")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    projects: Mapped[list["Project"]] = relationship(back_populates="workspace")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    daily_report_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="projects")
    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "email", name="uq_project_member_email"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(80), default="member")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="members")


class Integration(Base):
    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", "external_id", name="uq_project_provider_ext"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    provider: Mapped[Provider] = mapped_column(Enum(Provider, native_enum=False), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200))
    encrypted_credentials: Mapped[bytes | None] = mapped_column(nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(IntegrationStatus, native_enum=False), default=IntegrationStatus.ACTIVE, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceItem(Base):
    __tablename__ = "source_items"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_source_provider_external"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integrations.id"), nullable=True, index=True
    )
    provider: Mapped[Provider] = mapped_column(Enum(Provider, native_enum=False), index=True)
    external_id: Mapped[str] = mapped_column(String(500))
    kind: Mapped[SourceItemKind] = mapped_column(Enum(SourceItemKind, native_enum=False))
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[SourceItemStatus] = mapped_column(
        Enum(SourceItemStatus, native_enum=False), default=SourceItemStatus.NORMALIZED, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BriefRevision(Base):
    __tablename__ = "brief_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision_hash", name="uq_project_revision_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    title: Mapped[str] = mapped_column(String(300))
    revision_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[BriefRevisionStatus] = mapped_column(
        Enum(BriefRevisionStatus, native_enum=False),
        default=BriefRevisionStatus.PENDING_APPROVAL,
        index=True,
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    approver_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_item_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BriefApproval(Base):
    __tablename__ = "brief_approvals"
    __table_args__ = (
        UniqueConstraint("brief_revision_id", "project_member_id", name="uq_revision_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    brief_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brief_revisions.id"), index=True
    )
    project_member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_members.id"))
    revision_hash: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("project_id", "brief_revision_id", "channel", name="uq_daily_notice"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    brief_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brief_revisions.id"))
    channel: Mapped[str] = mapped_column(String(80), default="discord")
    external_channel_id: Mapped[str] = mapped_column(String(255))
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"
    __table_args__ = (UniqueConstraint("job_name", "run_key", name="uq_scheduler_job_run"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_name: Mapped[str] = mapped_column(String(120), index=True)
    run_key: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
