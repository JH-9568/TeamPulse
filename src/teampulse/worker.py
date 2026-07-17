import asyncio
import uuid

from celery import Celery
from celery.schedules import crontab

from teampulse.briefs.service import build_daily_revision
from teampulse.config import get_settings
from teampulse.db import SessionFactory
from teampulse.integrations.discord import poll_discord_integration
from teampulse.notifications.discord import send_discord_brief_notification
from teampulse.scheduler import run_daily_project_briefs
from teampulse.sources.service import list_source_items

settings = get_settings()
celery_app = Celery("teampulse", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Seoul",
    task_track_started=True,
    beat_schedule={
        "run-daily-project-briefs": {
            "task": "teampulse.run_daily_project_briefs",
            "schedule": crontab(
                hour=settings.daily_brief_hour,
                minute=settings.daily_brief_minute,
            ),
        },
    },
)


@celery_app.task(name="teampulse.generate_daily_brief")
def generate_daily_brief(project_id: str) -> str:
    return asyncio.run(_generate_daily_brief(uuid.UUID(project_id)))


async def _generate_daily_brief(project_id: uuid.UUID) -> str:
    async with SessionFactory() as session:
        source_items = await list_source_items(session, project_id)
        revision = await build_daily_revision(session, project_id, source_items, settings=settings)
        return str(revision.id)


@celery_app.task(name="teampulse.generate_daily_brief_and_notify")
def generate_daily_brief_and_notify(project_id: str) -> str:
    return asyncio.run(_generate_daily_brief_and_notify(uuid.UUID(project_id)))


async def _generate_daily_brief_and_notify(project_id: uuid.UUID) -> str:
    async with SessionFactory() as session:
        source_items = await list_source_items(session, project_id)
        revision = await build_daily_revision(session, project_id, source_items, settings=settings)
        await send_discord_brief_notification(session, revision.id, settings)
        return str(revision.id)


@celery_app.task(name="teampulse.poll_discord_channel")
def poll_discord_channel(integration_id: str) -> int:
    return asyncio.run(_poll_discord_channel(uuid.UUID(integration_id)))


async def _poll_discord_channel(integration_id: uuid.UUID) -> int:
    async with SessionFactory() as session:
        result = await poll_discord_integration(session, integration_id, settings)
        return result.stored


@celery_app.task(name="teampulse.run_daily_project_briefs")
def run_daily_project_briefs_task() -> dict:
    return asyncio.run(_run_daily_project_briefs_task())


async def _run_daily_project_briefs_task() -> dict:
    async with SessionFactory() as session:
        result = await run_daily_project_briefs(session, settings)
        return result.model_dump(mode="json")
