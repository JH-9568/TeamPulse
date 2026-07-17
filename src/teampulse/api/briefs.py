import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teampulse.briefs.service import (
    approval_state,
    approve_revision,
    build_daily_revision,
    create_revision,
)
from teampulse.config import Settings, get_settings
from teampulse.db import get_session
from teampulse.models import BriefRevision, Project
from teampulse.notifications.discord import send_discord_brief_notification
from teampulse.schemas import (
    ApprovalRead,
    BriefEditRequest,
    BriefGenerateRequest,
    BriefRevisionRead,
    DiscordNotificationRead,
)
from teampulse.sources.service import list_source_items

router = APIRouter(prefix="/api/v1/projects/{project_id}/briefs", tags=["briefs"])


@router.post("/generate", response_model=BriefRevisionRead, status_code=status.HTTP_201_CREATED)
async def generate_brief(
    project_id: uuid.UUID,
    payload: BriefGenerateRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BriefRevision:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    source_items = await list_source_items(session, project_id, payload.since, payload.until)
    revision = await build_daily_revision(session, project_id, source_items, settings=settings)
    return revision


@router.get("", response_model=list[BriefRevisionRead])
async def list_briefs(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[BriefRevision]:
    result = await session.execute(
        select(BriefRevision)
        .where(BriefRevision.project_id == project_id)
        .order_by(BriefRevision.version.desc())
    )
    return list(result.scalars().all())


@router.get("/{revision_id}", response_model=BriefRevisionRead)
async def get_brief(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> BriefRevision:
    revision = await session.get(BriefRevision, revision_id)
    if revision is None or revision.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brief revision not found")
    return revision


@router.post(
    "/{revision_id}/edit",
    response_model=BriefRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
async def edit_brief(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: BriefEditRequest,
    session: AsyncSession = Depends(get_session),
) -> BriefRevision:
    existing = await session.get(BriefRevision, revision_id)
    if existing is None or existing.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brief revision not found")
    revision = await create_revision(
        session,
        project_id,
        payload.content,
        [],
        payload.created_by,
        source_item_ids=existing.source_item_ids,
    )
    return revision


@router.post("/{revision_id}/approve", response_model=ApprovalRead)
async def approve_brief(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    x_teampulse_member_id: uuid.UUID = Header(alias="X-TeamPulse-Member-ID"),
    session: AsyncSession = Depends(get_session),
) -> ApprovalRead:
    revision = await session.get(BriefRevision, revision_id)
    if revision is None or revision.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brief revision not found")
    try:
        return await approve_revision(session, revision_id, x_teampulse_member_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{revision_id}/approval-state", response_model=ApprovalRead)
async def get_approval_state(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ApprovalRead:
    revision = await session.get(BriefRevision, revision_id)
    if revision is None or revision.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brief revision not found")
    return await approval_state(session, revision)


@router.post("/{revision_id}/notify-discord", response_model=DiscordNotificationRead)
async def notify_brief_to_discord(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DiscordNotificationRead:
    revision = await session.get(BriefRevision, revision_id)
    if revision is None or revision.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brief revision not found")
    try:
        result = await send_discord_brief_notification(session, revision_id, settings)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return DiscordNotificationRead.model_validate(result)
