import json
from datetime import UTC, datetime
from typing import Any

import httpx

from teampulse.connectors.base import NormalizedSourceItem
from teampulse.models import Provider, SourceItemKind


class FigmaWebhookConnector:
    provider = Provider.FIGMA

    def verify(self, body: bytes, headers: dict[str, str], secret: str) -> bool:
        del headers
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return False
        return payload.get("passcode") == secret

    def normalize_webhook(
        self,
        *,
        project_id: str,
        integration_id: str | None,
        body: bytes,
        headers: dict[str, str],
    ) -> list[NormalizedSourceItem]:
        del headers
        payload = json.loads(body)
        event_type = payload.get("event_type", "UNKNOWN")
        if event_type == "PING":
            return []
        occurred_at = parse_dt(payload.get("timestamp") or payload.get("created_at"))
        file_key = payload.get("file_key") or "unknown-file"
        webhook_id = payload.get("webhook_id", "unknown-webhook")
        event_id = (
            payload.get("comment_id")
            or payload.get("version_id")
            or payload.get("timestamp")
            or "unknown-event"
        )
        external_id = f"figma:{webhook_id}:{event_type}:{file_key}:{event_id}"
        kind = (
            SourceItemKind.DESIGN_COMMENT
            if event_type == "FILE_COMMENT"
            else SourceItemKind.DESIGN_UPDATE
        )
        body_text = comment_text(payload.get("comment", [])) if event_type == "FILE_COMMENT" else ""
        return [
            NormalizedSourceItem(
                project_id=project_id,
                integration_id=integration_id,
                provider=self.provider,
                external_id=external_id,
                kind=kind,
                title=f"Figma {event_type}: {payload.get('file_name', file_key)}",
                body=body_text,
                source_url=f"https://www.figma.com/file/{file_key}",
                occurred_at=occurred_at,
                actor=payload.get("triggered_by") or {},
                metadata={
                    "event_type": event_type,
                    "file_key": file_key,
                    "file_name": payload.get("file_name"),
                    "comment_id": payload.get("comment_id"),
                },
                raw_payload=payload,
            )
        ]


def comment_text(fragments: list[dict]) -> str:
    parts: list[str] = []
    for fragment in fragments:
        if "text" in fragment:
            parts.append(str(fragment["text"]))
        elif "mention" in fragment:
            parts.append(f"@{fragment['mention']}")
    return "".join(parts).strip()


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FigmaClient:
    base_url = "https://api.figma.com/v1"

    async def fetch_file(self, *, access_token: str, file_key: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20) as client:
            response = await client.get(
                f"/files/{file_key}",
                headers={"X-Figma-Token": access_token},
            )
            response.raise_for_status()
            return response.json()

    async def fetch_comments(self, *, access_token: str, file_key: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20) as client:
            response = await client.get(
                f"/files/{file_key}/comments",
                headers={"X-Figma-Token": access_token},
            )
            response.raise_for_status()
            payload = response.json()
            return list(payload.get("comments", []))
