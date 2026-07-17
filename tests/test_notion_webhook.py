import hmac
import json
from hashlib import sha256

from teampulse.connectors.notion import NotionWebhookConnector
from teampulse.models import Project, Provider, SourceItemKind, Workspace
from teampulse.schemas import SourceItemCreate
from teampulse.sources.service import store_source_item


def test_notion_webhook_verifies_signature_and_ignores_verification_payload():
    connector = NotionWebhookConnector()
    body = json.dumps({"verification_token": "notion-secret"}).encode()
    signature = "sha256=" + hmac.new(b"notion-secret", body, sha256).hexdigest()

    assert connector.verify(body, {"x-notion-signature": signature}, "notion-secret") is True
    assert connector.verify(body, {"x-notion-signature": signature}, "wrong") is False
    assert (
        connector.normalize_webhook(
            project_id="00000000-0000-0000-0000-000000000001",
            integration_id=None,
            body=body,
            headers={},
        )
        == []
    )


async def test_notion_page_event_normalizes_and_stores_idempotently(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Launch")
    session.add(project)
    await session.commit()

    connector = NotionWebhookConnector()
    body = json.dumps(
        {
            "id": "event-1",
            "type": "page.content_updated",
            "timestamp": "2026-07-18T10:00:00Z",
            "actor": {"id": "user-1", "name": "Jin"},
            "entity": {
                "id": "page-1",
                "url": "https://www.notion.so/page-1",
                "title": "Sprint plan",
            },
        }
    ).encode()

    normalized = connector.normalize_webhook(
        project_id=str(project.id),
        integration_id=None,
        body=body,
        headers={},
    )[0]

    assert normalized.provider == Provider.NOTION
    assert normalized.kind == SourceItemKind.PLANNING_DOC
    assert normalized.external_id == "notion:event-1:page.content_updated"
    assert normalized.source_url == "https://www.notion.so/page-1"

    data = normalized.model_dump(exclude={"project_id", "integration_id"})
    first, first_duplicate = await store_source_item(
        session,
        SourceItemCreate(**data, project_id=project.id),
    )
    second, second_duplicate = await store_source_item(
        session,
        SourceItemCreate(**data, project_id=project.id),
    )

    assert first_duplicate is False
    assert second_duplicate is True
    assert first.id == second.id


def test_notion_database_event_maps_to_task_change():
    connector = NotionWebhookConnector()
    body = json.dumps(
        {
            "id": "event-2",
            "type": "database.schema_updated",
            "timestamp": "2026-07-18T10:00:00Z",
            "entity": {"id": "database-1"},
        }
    ).encode()

    normalized = connector.normalize_webhook(
        project_id="00000000-0000-0000-0000-000000000001",
        integration_id=None,
        body=body,
        headers={},
    )[0]

    assert normalized.kind == SourceItemKind.TASK_CHANGE
