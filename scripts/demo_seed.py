import asyncio
from datetime import UTC, datetime

from teampulse.briefs.service import build_daily_revision
from teampulse.db import SessionFactory
from teampulse.models import Project, ProjectMember, Provider, SourceItemKind, Workspace
from teampulse.schemas import SourceItemCreate
from teampulse.sources.service import store_source_item


async def main() -> None:
    async with SessionFactory() as session:
        workspace = Workspace(name="TeamPulse Demo")
        session.add(workspace)
        await session.flush()

        project = Project(
            workspace_id=workspace.id,
            name="Demo Project",
            description="Seeded demo project for local TeamPulse development.",
            daily_report_channel_id="replace-with-discord-channel-id",
        )
        session.add(project)
        await session.flush()

        members = [
            ProjectMember(
                project_id=project.id,
                display_name="Designer",
                email="designer@example.com",
            ),
            ProjectMember(
                project_id=project.id,
                display_name="Planner",
                email="planner@example.com",
            ),
        ]
        session.add_all(members)
        await session.commit()

        source_items = []
        for item in [
            SourceItemCreate(
                project_id=project.id,
                provider=Provider.DISCORD,
                external_id=f"demo:discord:{project.id}:1",
                kind=SourceItemKind.MEETING_MESSAGE,
                title="Discord meeting decision",
                body="Decision: Onboarding will use the second Figma draft.",
                occurred_at=datetime.now(UTC),
                source_url="https://discord.com/channels/demo/demo/demo",
            ),
            SourceItemCreate(
                project_id=project.id,
                provider=Provider.FIGMA,
                external_id=f"demo:figma:{project.id}:1",
                kind=SourceItemKind.DESIGN_COMMENT,
                title="Figma design comment",
                body="TODO: Confirm CTA copy before implementation.",
                occurred_at=datetime.now(UTC),
                source_url="https://www.figma.com/file/demo",
            ),
            SourceItemCreate(
                project_id=project.id,
                provider=Provider.NOTION,
                external_id=f"demo:notion:{project.id}:1",
                kind=SourceItemKind.TASK_CHANGE,
                title="Notion task update",
                body="Task deadline moved to Friday; owner is Planner.",
                occurred_at=datetime.now(UTC),
                source_url="https://www.notion.so/demo",
            ),
        ]:
            source_item, _ = await store_source_item(session, item)
            source_items.append(source_item)

        revision = await build_daily_revision(session, project.id, source_items)
        print("Seeded TeamPulse demo data")
        print(f"Workspace ID: {workspace.id}")
        print(f"Project ID: {project.id}")
        print(f"Brief revision ID: {revision.id}")


if __name__ == "__main__":
    asyncio.run(main())
