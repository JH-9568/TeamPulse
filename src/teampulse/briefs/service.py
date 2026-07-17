import hashlib
import json
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teampulse.briefs.ai_summarizer import OpenAICompatibleBriefBuilder
from teampulse.briefs.summarizer import StructuredBriefBuilder
from teampulse.config import Settings
from teampulse.models import (
    BriefApproval,
    BriefRevision,
    BriefRevisionStatus,
    ProjectMember,
    SourceItem,
    utcnow,
)
from teampulse.schemas import ApprovalRead, BriefContent


def revision_hash(content: dict) -> str:
    payload = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def build_daily_revision(
    session: AsyncSession,
    project_id: uuid.UUID,
    source_items: Sequence[SourceItem],
    created_by: str = "system",
    settings: Settings | None = None,
) -> BriefRevision:
    content = await build_brief_content(source_items, settings)
    return await create_revision(session, project_id, content, source_items, created_by)


async def build_brief_content(
    source_items: Sequence[SourceItem],
    settings: Settings | None = None,
) -> BriefContent:
    if settings and settings.ai_summarizer_url:
        try:
            return await OpenAICompatibleBriefBuilder(settings).build(source_items)
        except Exception:
            pass
    return StructuredBriefBuilder().build(source_items)


async def create_revision(
    session: AsyncSession,
    project_id: uuid.UUID,
    content: BriefContent,
    source_items: Sequence[SourceItem],
    created_by: str = "system",
    source_item_ids: Sequence[str] | None = None,
) -> BriefRevision:
    await supersede_pending_revisions(session, project_id)
    version_result = await session.execute(
        select(func.count())
        .select_from(BriefRevision)
        .where(BriefRevision.project_id == project_id)
    )
    version = int(version_result.scalar_one()) + 1
    snapshot = await active_approver_snapshot(session, project_id)
    content_dict = content.model_dump(mode="json")
    revision = BriefRevision(
        project_id=project_id,
        version=version,
        title=f"Daily project brief v{version}",
        revision_hash=revision_hash(content_dict),
        status=BriefRevisionStatus.PENDING_APPROVAL,
        content=content_dict,
        approver_snapshot=snapshot,
        source_item_ids=list(source_item_ids)
        if source_item_ids is not None
        else [str(item.id) for item in source_items],
        created_by=created_by,
    )
    session.add(revision)
    await session.commit()
    await session.refresh(revision)
    return revision


async def supersede_pending_revisions(session: AsyncSession, project_id: uuid.UUID) -> None:
    result = await session.execute(
        select(BriefRevision).where(
            BriefRevision.project_id == project_id,
            BriefRevision.status.in_(
                [BriefRevisionStatus.DRAFT, BriefRevisionStatus.PENDING_APPROVAL]
            ),
        )
    )
    for revision in result.scalars().all():
        revision.status = BriefRevisionStatus.SUPERSEDED


async def active_approver_snapshot(session: AsyncSession, project_id: uuid.UUID) -> list[dict]:
    result = await session.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.active.is_(True),
        )
    )
    return [
        {
            "project_member_id": str(member.id),
            "display_name": member.display_name,
            "email": member.email,
            "role": member.role,
        }
        for member in result.scalars().all()
    ]


async def approve_revision(
    session: AsyncSession, revision_id: uuid.UUID, project_member_id: uuid.UUID
) -> ApprovalRead:
    revision = await session.get(BriefRevision, revision_id)
    if revision is None:
        raise ValueError("Brief revision not found")
    if revision.status != BriefRevisionStatus.PENDING_APPROVAL:
        raise ValueError("Only pending revisions can be approved")

    required_ids = {entry["project_member_id"] for entry in revision.approver_snapshot}
    if str(project_member_id) not in required_ids:
        raise ValueError("Member is not part of this revision approver snapshot")

    existing = await session.execute(
        select(BriefApproval).where(
            BriefApproval.brief_revision_id == revision_id,
            BriefApproval.project_member_id == project_member_id,
        )
    )
    if existing.scalar_one_or_none() is None:
        session.add(
            BriefApproval(
                brief_revision_id=revision.id,
                project_member_id=project_member_id,
                revision_hash=revision.revision_hash,
            )
        )
        await session.flush()

    state = await approval_state(session, revision)
    if state.approved_count == state.required_count:
        revision.status = BriefRevisionStatus.CONFIRMED
        revision.confirmed_at = utcnow()
        await session.commit()
        await session.refresh(revision)
    else:
        await session.commit()
    return await approval_state(session, revision)


async def approval_state(session: AsyncSession, revision: BriefRevision) -> ApprovalRead:
    result = await session.execute(
        select(BriefApproval.project_member_id).where(
            BriefApproval.brief_revision_id == revision.id,
            BriefApproval.revision_hash == revision.revision_hash,
        )
    )
    approved_ids = {str(member_id) for member_id in result.scalars().all()}
    required_ids = {entry["project_member_id"] for entry in revision.approver_snapshot}
    return ApprovalRead(
        revision_id=revision.id,
        revision_hash=revision.revision_hash,
        required_count=len(required_ids),
        approved_count=len(approved_ids),
        pending_member_ids=sorted(required_ids - approved_ids),
        status=revision.status,
    )
