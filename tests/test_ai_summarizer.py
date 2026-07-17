from datetime import UTC, datetime

import httpx

from teampulse.briefs.ai_summarizer import OpenAICompatibleBriefBuilder
from teampulse.config import Settings
from teampulse.models import Project, Provider, SourceItemKind, Workspace
from teampulse.schemas import SourceItemCreate
from teampulse.sources.service import store_source_item


async def test_openai_compatible_brief_builder_parses_json_content(session):
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
            external_id="ai:source:1",
            kind=SourceItemKind.MEETING_MESSAGE,
            title="Decision",
            body="Decision: use AI summarizer.",
            occurred_at=datetime.now(UTC),
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"sections":[{"key":"decisions","title":"Decisions","claims":'
                            '[{"text":"Use AI summarizer.","status":"ai_inference",'
                            f'"source_item_ids":["{source_item.id}"]}}]}}],'
                            '"source_window":{"builder":"ai"},'
                            '"diff_from_last_confirmed":[]}'
                        )
                    }
                }
            ]
        }
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        builder = OpenAICompatibleBriefBuilder(
            Settings(
                ai_summarizer_url="https://ai.example.test/chat/completions",
                ai_summarizer_api_key="test-key",
            ),
            client=client,
        )
        content = await builder.build([source_item])

    assert content.sections[0].key == "decisions"
    assert content.sections[0].claims[0].source_item_ids == [str(source_item.id)]
    assert content.source_window["builder"] == "ai"
