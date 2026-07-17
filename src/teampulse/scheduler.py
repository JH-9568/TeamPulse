import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teampulse.briefs.service import build_daily_revision
from teampulse.config import Settings
from teampulse.integrations.discord import DiscordPollResult, poll_discord_integration
from teampulse.models import Integration, IntegrationStatus, Project, Provider
from teampulse.notifications.discord import (
    DiscordNotificationResult,
    send_discord_brief_notification,
)
from teampulse.sources.service import list_source_items

Poller = Callable[[AsyncSession, uuid.UUID, Settings], Awaitable[DiscordPollResult]]
Notifier = Callable[[AsyncSession, uuid.UUID, Settings], Awaitable[DiscordNotificationResult]]


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
    poller: Poller | None = None,
    notifier: Notifier | None = None,
    now: datetime | None = None,
) -> DailySchedulerResult:
    started_at = now or datetime.now(UTC)
    poller = poller or poll_discord_integration
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
                poller=poller,
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
    poller: Poller,
    notifier: Notifier,
    now: datetime,
) -> DailyProjectRun:
    run = DailyProjectRun(project_id=project.id)
    integrations = await list_active_discord_integrations(session, project.id)

    for integration in integrations:
        try:
            poll_result = await poller(session, integration.id, settings)
        except Exception as exc:  # noqa: BLE001 - scheduler should continue with other projects
            run.errors.append(f"Discord integration {integration.id}: {exc}")
            continue
        run.integrations_polled += 1
        run.source_items_stored += poll_result.stored

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


async def list_active_discord_integrations(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> list[Integration]:
    result = await session.execute(
        select(Integration).where(
            Integration.project_id == project_id,
            Integration.provider == Provider.DISCORD,
            Integration.status == IntegrationStatus.ACTIVE,
        )
    )
    return list(result.scalars().all())
