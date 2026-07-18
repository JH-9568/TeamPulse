from datetime import UTC, datetime

from openbrief.briefs.service import build_daily_revision
from openbrief.models import Project, Provider, SourceItemKind, Workspace
from openbrief.schemas import SourceItemCreate
from openbrief.sources.service import store_source_item


async def test_structured_brief_builder_routes_key_signals_to_sections(session):
    workspace = Workspace(name="Acme")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Launch")
    session.add(project)
    await session.commit()

    source_items = []
    for payload in [
        SourceItemCreate(
            project_id=project.id,
            provider=Provider.DISCORD,
            external_id="discord:decision",
            kind=SourceItemKind.MEETING_MESSAGE,
            title="Meeting",
            body="Decision: onboarding uses variant B.",
            occurred_at=datetime.now(UTC),
        ),
        SourceItemCreate(
            project_id=project.id,
            provider=Provider.FIGMA,
            external_id="figma:todo",
            kind=SourceItemKind.DESIGN_COMMENT,
            title="Figma comment",
            body="TODO: CTA copy 확인",
            occurred_at=datetime.now(UTC),
        ),
        SourceItemCreate(
            project_id=project.id,
            provider=Provider.DISCORD,
            external_id="discord:blocker",
            kind=SourceItemKind.MEETING_MESSAGE,
            title="Blocker",
            body="blocked by API permission review",
            occurred_at=datetime.now(UTC),
        ),
        SourceItemCreate(
            project_id=project.id,
            provider=Provider.NOTION,
            external_id="notion:done",
            kind=SourceItemKind.TASK_CHANGE,
            title="Launch checklist",
            body="완료: README 보안 안내 정리",
            occurred_at=datetime.now(UTC),
            metadata={"status": "완료"},
        ),
        SourceItemCreate(
            project_id=project.id,
            provider=Provider.GITHUB,
            external_id="github:pr",
            kind=SourceItemKind.TASK_CHANGE,
            title="Pull request review",
            body="PR needs review before release",
            occurred_at=datetime.now(UTC),
        ),
    ]:
        source_item, _ = await store_source_item(session, payload)
        source_items.append(source_item)

    revision = await build_daily_revision(session, project.id, source_items)
    sections = {section["key"]: section for section in revision.content["sections"]}

    assert len(sections["decisions"]["claims"]) == 1
    assert len(sections["tasks"]["claims"]) == 2
    assert len(sections["completed"]["claims"]) == 1
    assert len(sections["schedule_risks"]["claims"]) == 1
    assert sections["tasks"]["claims"][0]["source_item_ids"] == [str(source_items[1].id)]
    assert sections["tasks"]["claims"][0]["text"].startswith("Figma 디자인 맥락")
    assert sections["completed"]["claims"][0]["text"].startswith("Notion 업무/문서")
