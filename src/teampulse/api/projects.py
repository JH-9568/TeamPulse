import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teampulse.config import Settings, get_settings
from teampulse.db import get_session
from teampulse.integrations.discord import poll_discord_integration
from teampulse.integrations.figma import sync_figma_integration
from teampulse.models import Integration, Project, ProjectMember, Provider, Workspace
from teampulse.schemas import (
    IntegrationCreate,
    IntegrationPollRead,
    IntegrationRead,
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectRead,
    WorkspaceCreate,
    WorkspaceRead,
)
from teampulse.security import CredentialCipher

router = APIRouter(prefix="/api/v1", tags=["projects"])


@router.post("/workspaces", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate, session: AsyncSession = Depends(get_session)
) -> Workspace:
    workspace = Workspace(name=payload.name, timezone=payload.timezone)
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, session: AsyncSession = Depends(get_session)
) -> Project:
    if await session.get(Workspace, payload.workspace_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    project = Project(**payload.model_dump())
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.post(
    "/projects/{project_id}/members",
    response_model=ProjectMemberRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_member(
    project_id: uuid.UUID,
    payload: ProjectMemberCreate,
    session: AsyncSession = Depends(get_session),
) -> ProjectMember:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    member = ProjectMember(project_id=project_id, **payload.model_dump())
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return member


@router.get("/projects/{project_id}/members", response_model=list[ProjectMemberRead])
async def list_members(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[ProjectMember]:
    result = await session.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    )
    return list(result.scalars().all())


@router.post(
    "/projects/{project_id}/integrations",
    response_model=IntegrationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_integration(
    project_id: uuid.UUID,
    payload: IntegrationCreate,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Integration:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    encrypted_credentials = None
    if payload.credentials:
        if settings.token_encryption_key is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "TOKEN_ENCRYPTION_KEY is required when storing credentials",
            )
        cipher = CredentialCipher(settings.token_encryption_key.get_secret_value())
        encrypted_credentials = cipher.encrypt(json.dumps(payload.credentials))
    integration = Integration(
        project_id=project_id,
        provider=payload.provider,
        external_id=payload.external_id,
        name=payload.name,
        encrypted_credentials=encrypted_credentials,
        config=payload.config,
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return integration


@router.get("/projects/{project_id}/integrations", response_model=list[IntegrationRead])
async def list_integrations(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[Integration]:
    result = await session.execute(select(Integration).where(Integration.project_id == project_id))
    return list(result.scalars().all())


@router.post(
    "/projects/{project_id}/integrations/{integration_id}/poll",
    response_model=IntegrationPollRead,
)
async def poll_integration(
    project_id: uuid.UUID,
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> IntegrationPollRead:
    integration = await session.get(Integration, integration_id)
    if integration is None or integration.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    try:
        if integration.provider == Provider.DISCORD:
            result = await poll_discord_integration(session, integration_id, settings)
            return IntegrationPollRead(
                integration_id=result.integration_id,
                provider=Provider.DISCORD,
                channel_id=result.channel_id,
                fetched=result.fetched,
                stored=result.stored,
                duplicates=result.duplicates,
                checkpoint=result.last_message_id,
            )
        if integration.provider == Provider.FIGMA:
            result = await sync_figma_integration(session, integration_id, settings)
            return IntegrationPollRead(
                integration_id=result.integration_id,
                provider=Provider.FIGMA,
                file_key=result.file_key,
                fetched=result.fetched,
                stored=result.stored,
                duplicates=result.duplicates,
                checkpoint=result.last_synced_at,
            )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provider polling is not implemented")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
