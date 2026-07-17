from teampulse.config import Settings
from teampulse.integrations.figma import sync_figma_integration
from teampulse.models import Integration, Project, Provider, Workspace


class FakeFigmaClient:
    async def fetch_file(self, *, access_token: str, file_key: str) -> dict:
        assert access_token == "figma-token"
        assert file_key == "file-1"
        return {
            "name": "Main design",
            "lastModified": "2026-07-18T10:00:00Z",
            "version": "v1",
            "thumbnailUrl": "https://example.com/thumb.png",
        }

    async def fetch_comments(self, *, access_token: str, file_key: str) -> list[dict]:
        assert access_token == "figma-token"
        assert file_key == "file-1"
        return [
            {
                "id": "comment-1",
                "message": "TODO: CTA 확인",
                "created_at": "2026-07-18T10:01:00Z",
                "user": {"handle": "Mina"},
            },
            {
                "id": "comment-2",
                "message": "Decision: use variant B",
                "created_at": "2026-07-18T10:02:00Z",
                "user": {"handle": "Jin"},
            },
        ]


async def test_sync_figma_integration_fetches_file_and_comments(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Launch")
    session.add(project)
    await session.flush()
    integration = Integration(
        project_id=project.id,
        provider=Provider.FIGMA,
        external_id="file-1",
        name="Main design",
        config={"file_key": "file-1"},
    )
    session.add(integration)
    await session.commit()

    result = await sync_figma_integration(
        session,
        integration.id,
        Settings(figma_access_token="figma-token"),
        FakeFigmaClient(),
    )

    assert result.file_key == "file-1"
    assert result.fetched == 3
    assert result.stored == 3
    assert result.duplicates == 0

    duplicate_result = await sync_figma_integration(
        session,
        integration.id,
        Settings(figma_access_token="figma-token"),
        FakeFigmaClient(),
    )

    assert duplicate_result.fetched == 3
    assert duplicate_result.stored == 0
    assert duplicate_result.duplicates == 3
