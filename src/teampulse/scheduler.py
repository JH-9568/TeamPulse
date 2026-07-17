import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teampulse.briefs.service import build_daily_revision
from teampulse.config import Settings
from teampulse.integrations.discord import poll_discord_integration
from teampulse.integrations.figma import sync_figma_integration
from teampulse.integrations.notion import sync_notion_integration
from teampulse.models import Integration, IntegrationStatus, Project, Provider
from teampulse.notifications.discord import (
    DiscordNotificationResult,
    send_discord_brief_notification,
)
from teampulse.sources.service import list_source_items

IntegrationSyncer = Callable[
    [AsyncSession, uuid.UUID, Settings],
    Awaitable["IntegrationSyncResult"],
]
Notifier = Callable[[AsyncSession, uuid.UUID, Settings], Awaitable[DiscordNotificationResult]]


class IntegrationSyncResult(BaseModel):
    integration_id: uuid.UUID
    provider: Provider
    fetched: int
    stored: int
    duplicates: int


class DailyProjectRun(BaseModel):
    project_id: uuid.UUID
    integrations_polled: int = 0
    source_items_stored: int = 0
    brief_revision_id: uuid.UUID | None = None
    notification_delivered: bool = False
    skipped_reason: str | None = None
    errors: list[str] = Field(default_factory=list)


class DailySchedulerResult(BaseModel):
    started_at: datetime
    finished_at: datetime
    projects_seen: int
    projects_succeeded: int
    projects_failed: int
    projects_skipped: int
    integrations_polled: int
    source_items_stored: int
    revisions_created: int
    notifications_delivered: int
    project_runs: list[DailyProjectRun]


async def run_daily_project_briefs(
    session: AsyncSession,
    settings: Settings,
    *,
    integration_syncer: IntegrationSyncer | None = None,
    notifier: Notifier | None = None,
    now: datetime | None = None,
) -> DailySchedulerResult:
    started_at = now or datetime.now(UTC)
    integration_syncer = integration_syncer or sync_integration
    notifier = notifier or send_discord_brief_notification
    result = await session.execute(select(Project).where(Project.active.is_(True)))
    projects = result.scalars().all()
    runs: list[DailyProjectRun] = []

    for project in projects:
        runs.append(
            await run_one_project(
                session,
                settings,
                project,
                integration_syncer=integration_syncer,
                notifier=notifier,
                now=started_at,
            )
        )

    finished_at = datetime.now(UTC)
    return DailySchedulerResult(
        started_at=started_at,
        finished_at=finished_at,
        projects_seen=len(projects),
        projects_succeeded=sum(1 for run in runs if run.brief_revision_id and not run.errors),
        projects_failed=sum(1 for run in runs if run.errors),
        projects_skipped=sum(1 for run in runs if run.skipped_reason),
        integrations_polled=sum(run.integrations_polled for run in runs),
        source_items_stored=sum(run.source_items_stored for run in runs),
        revisions_created=sum(1 for run in runs if run.brief_revision_id),
        notifications_delivered=sum(1 for run in runs if run.notification_delivered),
        project_runs=runs,
    )


async def run_one_project(
    session: AsyncSession,
    settings: Settings,
    project: Project,
    *,
    integration_syncer: IntegrationSyncer,
    notifier: Notifier,
    now: datetime,
) -> DailyProjectRun:
    run = DailyProjectRun(project_id=project.id)
    integrations = await list_active_ingest_integrations(session, project.id)

    for integration in integrations:
        try:
            sync_result = await integration_syncer(session, integration.id, settings)
        except Exception as exc:  # noqa: BLE001 - scheduler should continue with other projects
            run.errors.append(f"{integration.provider} integration {integration.id}: {exc}")
            continue
        run.integrations_polled += 1
        run.source_items_stored += sync_result.stored

    since = now - timedelta(days=1)
    source_items = await list_source_items(session, project.id, since=since, until=now)
    if not source_items:
        run.skipped_reason = "no_source_items_in_daily_window"
        return run

    try:
        revision = await build_daily_revision(session, project.id, source_items)
    except Exception as exc:  # noqa: BLE001
        run.errors.append(f"Brief generation failed: {exc}")
        return run

    run.brief_revision_id = revision.id
    try:
        notification = await notifier(session, revision.id, settings)
    except Exception as exc:  # noqa: BLE001
        run.errors.append(f"Discord notification failed: {exc}")
        return run
    run.notification_delivered = notification.delivered
    return run


async def sync_integration(
    session: AsyncSession,
    integration_id: uuid.UUID,
    settings: Settings,
) -> IntegrationSyncResult:
    integration = await session.get(Integration, integration_id)
    if integration is None:
        raise ValueError("Integration not found")
    if integration.provider == Provider.DISCORD:
        result = await poll_discord_integration(session, integration_id, settings)
        return IntegrationSyncResult(
            integration_id=result.integration_id,
            provider=Provider.DISCORD,
            fetched=result.fetched,
            stored=result.stored,
            duplicates=result.duplicates,
        )
    if integration.provider == Provider.FIGMA:
        result = await sync_figma_integration(session, integration_id, settings)
        return IntegrationSyncResult(
            integration_id=result.integration_id,
            provider=Provider.FIGMA,
            fetched=result.fetched,
            stored=result.stored,
            duplicates=result.duplicates,
        )
    if integration.provider == Provider.NOTION:
        result = await sync_notion_integration(session, integration_id, settings)
        return IntegrationSyncResult(
            integration_id=result.integration_id,
            provider=Provider.NOTION,
            fetched=result.fetched,
            stored=result.stored,
            duplicates=result.duplicates,
        )
    raise ValueError(f"Provider sync is not implemented: {integration.provider}")


async def list_active_ingest_integrations(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> list[Integration]:
    result = await session.execute(
        select(Integration).where(
            Integration.project_id == project_id,
            Integration.provider.in_([Provider.DISCORD, Provider.FIGMA, Provider.NOTION]),
            Integration.status == IntegrationStatus.ACTIVE,
        )
    )
    return list(result.scalars().all())
