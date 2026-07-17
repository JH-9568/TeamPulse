import uuid
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from teampulse.config import Settings
from teampulse.connectors.discord import DiscordClient
from teampulse.integrations.discord import decrypt_credentials
from teampulse.models import BriefRevision, Integration, NotificationDelivery, Project, Provider


class DiscordMessageSender(Protocol):
    async def send_message(
        self,
        *,
        bot_token: str,
        channel_id: str,
        content: str,
    ) -> dict[str, Any]:
        ...


class DiscordNotificationResult(BaseModel):
    brief_revision_id: uuid.UUID
    channel_id: str
    delivered: bool
    duplicate: bool
    external_message_id: str | None = None


async def send_discord_brief_notification(
    session: AsyncSession,
    revision_id: uuid.UUID,
    settings: Settings,
    client: DiscordMessageSender | None = None,
) -> DiscordNotificationResult:
    revision = await session.get(BriefRevision, revision_id)
    if revision is None:
        raise ValueError("Brief revision not found")

    project = await session.get(Project, revision.project_id)
    if project is None:
        raise ValueError("Project not found")

    channel_id = await resolve_discord_channel_id(session, project)
    existing = await find_existing_delivery(session, project.id, revision.id, channel_id)
    if existing:
        return DiscordNotificationResult(
            brief_revision_id=revision.id,
            channel_id=channel_id,
            delivered=False,
            duplicate=True,
            external_message_id=existing.payload.get("message_id"),
        )

    bot_token = await resolve_discord_bot_token(session, project.id, settings)
    message = format_brief_notification(project, revision)
    discord_client = client or DiscordClient()
    response = await discord_client.send_message(
        bot_token=bot_token,
        channel_id=channel_id,
        content=message,
    )

    delivery = NotificationDelivery(
        project_id=project.id,
        brief_revision_id=revision.id,
        channel="discord",
        external_channel_id=channel_id,
        payload={"message_id": response.get("id"), "response": response},
    )
    session.add(delivery)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return DiscordNotificationResult(
            brief_revision_id=revision.id,
            channel_id=channel_id,
            delivered=False,
            duplicate=True,
            external_message_id=response.get("id"),
        )

    return DiscordNotificationResult(
        brief_revision_id=revision.id,
        channel_id=channel_id,
        delivered=True,
        duplicate=False,
        external_message_id=response.get("id"),
    )


async def resolve_discord_channel_id(session: AsyncSession, project: Project) -> str:
    if project.daily_report_channel_id:
        return project.daily_report_channel_id
    result = await session.execute(
        select(Integration).where(
            Integration.project_id == project.id,
            Integration.provider == Provider.DISCORD,
        )
    )
    integration = result.scalars().first()
    if integration and integration.config.get("channel_id"):
        return str(integration.config["channel_id"])
    raise ValueError("No Discord report channel configured")


async def resolve_discord_bot_token(
    session: AsyncSession,
    project_id: uuid.UUID,
    settings: Settings,
) -> str:
    result = await session.execute(
        select(Integration).where(
            Integration.project_id == project_id,
            Integration.provider == Provider.DISCORD,
        )
    )
    for integration in result.scalars().all():
        credentials = decrypt_credentials(integration, settings)
        if credentials.get("bot_token"):
            return str(credentials["bot_token"])
    if settings.discord_bot_token:
        return settings.discord_bot_token.get_secret_value()
    raise ValueError("Discord bot token is required")


async def find_existing_delivery(
    session: AsyncSession,
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    channel_id: str,
) -> NotificationDelivery | None:
    result = await session.execute(
        select(NotificationDelivery).where(
            NotificationDelivery.project_id == project_id,
            NotificationDelivery.brief_revision_id == revision_id,
            NotificationDelivery.channel == "discord",
            NotificationDelivery.external_channel_id == channel_id,
        )
    )
    return result.scalar_one_or_none()


def format_brief_notification(project: Project, revision: BriefRevision) -> str:
    required_count = len(revision.approver_snapshot)
    source_count = len(revision.source_item_ids)
    return (
        f"[TeamPulse] {project.name} daily brief is ready.\n"
        f"- Revision: v{revision.version}\n"
        f"- Sources reviewed: {source_count}\n"
        f"- Required approvals: {required_count}\n"
        f"- Status: {revision.status.value}\n"
        "Open TeamPulse to review the cited brief and approve the revision."
    )
