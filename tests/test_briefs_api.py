from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from teampulse.briefs.service import build_daily_revision
from teampulse.db import get_session
from teampulse.main import create_app
from teampulse.models import Project, Provider, SourceItemKind, Workspace
from teampulse.schemas import SourceItemCreate
from teampulse.sources.service import store_source_item


async def test_get_brief_revision_detail(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Launch")
    session.add(project)
    await session.commit()
    source_item, _ = await store_source_item(
        session,
        SourceItemCreate(
            project_id=project.id,
            provider=Provider.DISCORD,
            external_id="discord:401",
            kind=SourceItemKind.MEETING_MESSAGE,
            title="Decision",
            body="결정: 브리프 단건 조회를 만든다.",
            occurred_at=datetime.now(UTC),
        ),
    )
    revision = await build_daily_revision(session, project.id, [source_item])

    app = create_app()

    async def override_session() -> AsyncIterator:
        yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/projects/{project.id}/briefs/{revision.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(revision.id)
    assert payload["project_id"] == str(project.id)
    assert payload["content"]["sections"]
