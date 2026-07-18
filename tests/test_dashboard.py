from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from teampulse.briefs.service import build_daily_revision
from teampulse.db import get_session
from teampulse.main import create_app
from teampulse.models import Project, ProjectMember, Provider, SourceItemKind, Workspace
from teampulse.schemas import SourceItemCreate
from teampulse.sources.service import store_source_item


async def test_project_dashboard_renders_latest_brief_and_sources(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Launch", description="Demo")
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, display_name="Alice", email="a@example.com"))
    await session.commit()
    source_item, _ = await store_source_item(
        session,
        SourceItemCreate(
            project_id=project.id,
            provider=Provider.DISCORD,
            external_id="dashboard:source:1",
            kind=SourceItemKind.MEETING_MESSAGE,
            title="Dashboard decision",
            body="Decision: show dashboard.",
            occurred_at=datetime.now(UTC),
        ),
    )
    await build_daily_revision(session, project.id, [source_item])

    app = create_app()

    async def override_session() -> AsyncIterator:
        yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/dashboard/projects/{project.id}")

    assert response.status_code == 200
    assert "Launch" in response.text
    assert "Latest Brief" in response.text
    assert "Dashboard decision" in response.text
    assert "Confirmation" in response.text
    assert "Alice" in response.text


async def test_dashboard_home_lists_projects(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Launch")
    session.add(project)
    await session.commit()

    app = create_app()

    async def override_session() -> AsyncIterator:
        yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert "TeamPulse" in response.text
    assert "Launch" in response.text
    assert f"/dashboard/projects/{project.id}" in response.text
