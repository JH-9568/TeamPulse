import json
from collections.abc import Sequence
from typing import Any

import httpx

from teampulse.config import Settings
from teampulse.models import SourceItem
from teampulse.schemas import BriefContent


class OpenAICompatibleBriefBuilder:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.ai_summarizer_url:
            raise ValueError("AI_SUMMARIZER_URL is required")
        self.settings = settings
        self.client = client

    async def build(self, source_items: Sequence[SourceItem]) -> BriefContent:
        payload = self._request_payload(source_items)
        headers = {"Content-Type": "application/json"}
        if self.settings.ai_summarizer_api_key:
            headers["Authorization"] = (
                f"Bearer {self.settings.ai_summarizer_api_key.get_secret_value()}"
            )

        if self.client:
            response = await self.client.post(
                self.settings.ai_summarizer_url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return self._parse_response(response.json())

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.settings.ai_summarizer_url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return self._parse_response(response.json())

    def _request_payload(self, source_items: Sequence[SourceItem]) -> dict[str, Any]:
        return {
            "model": self.settings.ai_summarizer_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You create TeamPulse daily project briefs. Return only JSON matching "
                        "this shape: "
                        "{sections:[{key,title,claims:[{text,status,source_item_ids}]}],"
                        "source_window:{},diff_from_last_confirmed:[]}. Every claim must include "
                        "source_item_ids or use status 'needs_confirmation'. Valid statuses are "
                        "confirmed, ai_inference, conflict, needs_confirmation."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_items": [
                                {
                                    "id": str(item.id),
                                    "provider": item.provider.value,
                                    "kind": item.kind.value,
                                    "title": item.title,
                                    "body": item.body,
                                    "source_url": item.source_url,
                                    "occurred_at": item.occurred_at.isoformat(),
                                    "actor": item.actor,
                                    "metadata": item.source_metadata,
                                }
                                for item in source_items
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }

    def _parse_response(self, payload: dict[str, Any]) -> BriefContent:
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return BriefContent.model_validate_json(content)
        return BriefContent.model_validate(content)
