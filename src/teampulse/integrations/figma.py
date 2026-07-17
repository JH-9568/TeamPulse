import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from teampulse.config import Settings
from teampulse.connectors.figma import FigmaClient
from teampulse.integrations.discord import decrypt_credentials
from teampulse.models import Integration, Provider, SourceItemKind
from teampulse.schemas import SourceItemCreate
from teampulse.sources.service import store_source_item


class FigmaApiClient(Protocol):
    async def fetch_file(self, *, access_token: str, file_key: str) -> dict[str, Any]: ...

    async def fetch_comments(self, *, access_token: str, file_key: str) -> list[dict[str, Any]]: ...


class FigmaSyncResult(BaseModel):
    integration_id: uuid.UUID
    file_key: str
    fetched: int
    stored: int
    duplicates: int
    last_synced_at: str


async def sync_figma_integration(
    session: AsyncSession,
    integration_id: uuid.UUID,
    settings: Settings,
    client: FigmaApiClient | None = None,
) -> FigmaSyncResult:
    integration = await session.get(Integration, integration_id)
    if integration is None:
        raise ValueError("Integration not found")
    if integration.provider != Provider.FIGMA:
        raise ValueError("Integration is not a Figma integration")

    integration_id_value = integration.id
    integration_config = dict(integration.config)
    file_key = integration_config.get("file_key") or integration.external_id
    if not file_key:
        raise ValueError("Figma integration config.file_key is required")

    credentials = decrypt_credentials(integration, settings)
    access_token = credentials.get("access_token")
    if not access_token and settings.figma_access_token:
        access_token = settings.figma_access_token.get_secret_value()
    if not access_token:
        raise ValueError("Figma access token is required")

    figma_client = client or FigmaClient()
    file_payload = await figma_client.fetch_file(access_token=access_token, file_key=file_key)
    comments = await figma_client.fetch_comments(access_token=access_token, file_key=file_key)

    stored = 0
    duplicates = 0
    items = [file_source_item(integration, file_key, file_payload)]
    items.extend(comment_source_item(integration, file_key, comment) for comment in comments)

    for item in items:
        _, duplicate = await store_source_item(session, item)
        stored += int(not duplicate)
        duplicates += int(duplicate)

    last_synced_at = datetime.now(UTC).isoformat()
    checkpoint = await session.get(Integration, integration_id_value)
    if checkpoint is None:
        raise ValueError("Integration not found")
    checkpoint.config = {
        **integration_config,
        "file_key": file_key,
        "last_synced_at": last_synced_at,
    }
    await session.commit()

    return FigmaSyncResult(
        integration_id=integration_id_value,
        file_key=file_key,
        fetched=len(items),
        stored=stored,
        duplicates=duplicates,
        last_synced_at=last_synced_at,
    )


def file_source_item(
    integration: Integration,
    file_key: str,
    payload: dict[str, Any],
) -> SourceItemCreate:
    occurred_at = parse_figma_dt(payload.get("lastModified"))
    version = payload.get("version") or payload.get("lastModified") or "unknown"
    return SourceItemCreate(
        project_id=integration.project_id,
        integration_id=integration.id,
        provider=Provider.FIGMA,
        external_id=f"figma:file:{file_key}:{version}",
        kind=SourceItemKind.DESIGN_UPDATE,
        title=f"Figma file updated: {payload.get('name', file_key)}",
        body=f"Last modified at {payload.get('lastModified', 'unknown')}",
        source_url=f"https://www.figma.com/file/{file_key}",
        occurred_at=occurred_at,
        metadata={
            "file_key": file_key,
            "file_name": payload.get("name"),
            "version": payload.get("version"),
            "thumbnail_url": payload.get("thumbnailUrl"),
        },
        raw_payload=payload,
    )


def comment_source_item(
    integration: Integration,
    file_key: str,
    comment: dict[str, Any],
) -> SourceItemCreate:
    comment_id = comment.get("id") or comment.get("comment_id") or "unknown"
    return SourceItemCreate(
        project_id=integration.project_id,
        integration_id=integration.id,
        provider=Provider.FIGMA,
        external_id=f"figma:comment:{file_key}:{comment_id}",
        kind=SourceItemKind.DESIGN_COMMENT,
        title=f"Figma comment: {comment_id}",
        body=str(comment.get("message") or ""),
        source_url=f"https://www.figma.com/file/{file_key}",
        occurred_at=parse_figma_dt(comment.get("created_at")),
        actor=comment.get("user") or {},
        metadata={
            "file_key": file_key,
            "comment_id": comment_id,
            "resolved_at": comment.get("resolved_at"),
        },
        raw_payload=comment,
    )


def parse_figma_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
