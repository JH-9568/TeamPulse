from datetime import UTC, datetime

from teampulse.briefs.service import build_daily_revision
from teampulse.config import Settings
from teampulse.models import (
    Integration,
    Project,
    ProjectMember,
    Provider,
    SourceItemKind,
    Workspace,
)
from teampulse.notifications.discord import send_discord_brief_notification
from teampulse.schemas import SourceItemCreate
from teampulse.sources.service import store_source_item


class FakeDiscordSender:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, *, bot_token: str, channel_id: str, content: str) -> dict:
        self.calls += 1
        assert bot_token == "test-token"
        assert channel_id == "channel-1"
        assert "daily brief is ready" in content
        return {"id": f"message-{self.calls}", "content": content}


async def test_discord_brief_notification_is_idempotent(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    await session.flush()
    project = Project(
        workspace_id=workspace.id,
        name="Launch",
        daily_report_channel_id="channel-1",
    )
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, display_name="Alice", email="a@example.com"))
    session.add(
        Integration(
            project_id=project.id,
            provider=Provider.DISCORD,
            external_id="channel-1",
            name="Project channel",
            config={"channel_id": "channel-1"},
        )
    )
    await session.commit()

    source_item, _ = await store_source_item(
        session,
        SourceItemCreate(
            project_id=project.id,
            provider=Provider.DISCORD,
            external_id="discord:201",
            kind=SourceItemKind.MEETING_MESSAGE,
            title="Discord message",
            body="결정: daily reminder를 보낸다.",
            occurred_at=datetime.now(UTC),
        ),
    )
    revision = await build_daily_revision(session, project.id, [source_item])
    sender = FakeDiscordSender()

    first = await send_discord_brief_notification(
        session,
        revision.id,
        Settings(discord_bot_token="test-token"),
        sender,
    )
    second = await send_discord_brief_notification(
        session,
        revision.id,
        Settings(discord_bot_token="test-token"),
        sender,
    )

    assert first.delivered is True
    assert first.duplicate is False
    assert second.delivered is False
    assert second.duplicate is True
    assert sender.calls == 1
