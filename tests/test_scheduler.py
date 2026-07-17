import uuid
from datetime import UTC, datetime

from teampulse.config import Settings
from teampulse.integrations.discord import DiscordPollResult
from teampulse.models import (
    Integration,
    Project,
    ProjectMember,
    Provider,
    SourceItemKind,
    Workspace,
)
from teampulse.notifications.discord import DiscordNotificationResult
from teampulse.scheduler import run_daily_project_briefs
from teampulse.schemas import SourceItemCreate
from teampulse.sources.service import store_source_item


async def test_daily_scheduler_polls_builds_and_notifies_active_projects(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    await session.flush()
    active_project = Project(
        workspace_id=workspace.id,
        name="Active",
        daily_report_channel_id="channel-1",
    )
    inactive_project = Project(workspace_id=workspace.id, name="Inactive", active=False)
    session.add_all([active_project, inactive_project])
    await session.flush()
    session.add(
        ProjectMember(
            project_id=active_project.id,
            display_name="Alice",
            email="alice@example.com",
        )
    )
    integration = Integration(
        project_id=active_project.id,
        provider=Provider.DISCORD,
        external_id="channel-1",
        name="Project channel",
        config={"channel_id": "channel-1"},
    )
    session.add(integration)
    await session.commit()

    now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)

    async def fake_poller(session, integration_id: uuid.UUID, settings: Settings):
        source_item, duplicate = await store_source_item(
            session,
            SourceItemCreate(
                project_id=active_project.id,
                integration_id=integration_id,
                provider=Provider.DISCORD,
                external_id="scheduler:discord:1",
                kind=SourceItemKind.MEETING_MESSAGE,
                title="Decision",
                body="Decision: scheduler creates daily briefs.",
                occurred_at=now,
            ),
        )
        assert source_item.id
        return DiscordPollResult(
            integration_id=integration_id,
            channel_id="channel-1",
            fetched=1,
            stored=0 if duplicate else 1,
            duplicates=1 if duplicate else 0,
            last_message_id="1",
        )

    async def fake_notifier(session, revision_id: uuid.UUID, settings: Settings):
        return DiscordNotificationResult(
            brief_revision_id=revision_id,
            channel_id="channel-1",
            delivered=True,
            duplicate=False,
            external_message_id="message-1",
        )

    result = await run_daily_project_briefs(
        session,
        Settings(discord_bot_token="test-token"),
        poller=fake_poller,
        notifier=fake_notifier,
        now=now,
    )

    assert result.projects_seen == 1
    assert result.projects_succeeded == 1
    assert result.integrations_polled == 1
    assert result.source_items_stored == 1
    assert result.revisions_created == 1
    assert result.notifications_delivered == 1
    assert result.project_runs[0].brief_revision_id is not None
