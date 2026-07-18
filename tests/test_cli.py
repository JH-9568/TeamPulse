import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openbrief import cli
from openbrief.models import (
    BriefRevision,
    Integration,
    Project,
    Provider,
    SourceItem,
    SourceItemKind,
)


def test_init_creates_local_config_and_sqlite_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))

    exit_code = cli.main(["init"])

    assert exit_code == 0
    assert (tmp_path / "config.toml").exists()
    assert (tmp_path / "openbrief.db").exists()
    assert "OpenBrief local app initialized" in capsys.readouterr().out


def test_status_reports_not_running_for_fresh_local_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))

    exit_code = cli.main(["status"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "OpenBrief is not running." in output
    assert str(Path(tmp_path) / "config.toml") in output


def test_default_config_uses_sqlite_database_in_local_home(tmp_path):
    config = cli.default_config(tmp_path)

    assert config.database_url.startswith("sqlite+aiosqlite:///")
    assert config.database_url.endswith("/openbrief.db")
    assert config.dashboard_url == "http://127.0.0.1:8000/dashboard"


def test_status_uses_runtime_dashboard_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))
    config = cli.default_config(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    cli.write_config(config)
    config.pid_path.write_text(str(os.getpid()), encoding="utf-8")
    config.run_path.write_text(
        json.dumps({"dashboard_url": "http://127.0.0.1:8010/dashboard"}),
        encoding="utf-8",
    )

    exit_code = cli.main(["status"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "OpenBrief is running" in output
    assert "http://127.0.0.1:8010/dashboard" in output


def test_setup_creates_project_and_integrations(tmp_path, monkeypatch):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))

    exit_code = cli.main(
        [
            "setup",
            "--project-name",
            "Launch",
            "--member",
            "JH:jh@example.com",
            "--figma-file-url",
            "https://www.figma.com/file/file-123/Mock",
            "--figma-token",
            "figma-token",
            "--notion-page-url",
            "https://www.notion.so/Sprint-abcdef1234567890abcdef1234567890",
            "--notion-token",
            "notion-token",
            "--discord-channel-id",
            "channel-1",
            "--discord-bot-token",
            "discord-token",
            "--github-repo",
            "JH-9568/OpenBrief",
            "--github-token",
            "github-token",
        ]
    )

    assert exit_code == 0
    projects, integrations = asyncio.run(load_projects_and_integrations(tmp_path))

    assert [project.name for project in projects] == ["Launch"]
    assert {integration.provider for integration in integrations} == {
        Provider.FIGMA,
        Provider.NOTION,
        Provider.DISCORD,
        Provider.GITHUB,
    }
    assert all(integration.encrypted_credentials for integration in integrations)


def test_sync_reports_provider_errors_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))
    cli.main(
        [
            "setup",
            "--project-name",
            "Launch",
            "--github-repo",
            "JH-9568/OpenBrief",
        ]
    )

    async def failing_sync_one(session, integration, settings):
        del session, integration, settings
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(cli, "sync_one_integration", failing_sync_one)

    exit_code = cli.main(["sync", "--provider", "github"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "error=network unavailable" in output


def test_setup_stores_openai_settings(tmp_path, monkeypatch):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))

    exit_code = cli.main(
        [
            "setup",
            "--project",
            "Launch",
            "--openai-api-key",
            "sk-test",
            "--ai-model",
            "gpt-test",
        ]
    )

    assert exit_code == 0
    config = cli.load_or_default_config(tmp_path)
    assert config.ai_summarizer_url == "https://api.openai.com/v1/chat/completions"
    assert config.ai_summarizer_api_key == "sk-test"
    assert config.ai_summarizer_model == "gpt-test"


def test_auth_stores_openai_settings_from_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "sk-prompt")

    exit_code = cli.main(["auth", "openai", "--ai-model", "gpt-prompt"])

    assert exit_code == 0
    config = cli.load_or_default_config(tmp_path)
    assert config.ai_summarizer_api_key == "sk-prompt"
    assert config.ai_summarizer_model == "gpt-prompt"


def test_auth_updates_existing_provider_integration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))
    cli.main(["setup", "--project", "Launch", "--github-repo", "JH-9568/OpenBrief"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "github-token")

    exit_code = cli.main(["auth", "github"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Stored github token for 1 integration" in output
    integrations = asyncio.run(load_projects_and_integrations(tmp_path))[1]
    assert integrations[0].encrypted_credentials


def test_brief_generates_revision_from_collected_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cli.HOME_ENV, str(tmp_path))
    cli.main(["setup", "--project", "Launch"])
    asyncio.run(add_source_item(tmp_path))

    exit_code = cli.main(["brief"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "brief Launch" in output
    assert asyncio.run(count_briefs(tmp_path)) == 1


async def load_projects_and_integrations(tmp_path):
    config = cli.load_or_default_config(tmp_path)
    engine = create_async_engine(config.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        projects = list((await session.execute(select(Project))).scalars().all())
        integrations = list((await session.execute(select(Integration))).scalars().all())
    await engine.dispose()
    return projects, integrations


async def add_source_item(tmp_path):
    config = cli.load_or_default_config(tmp_path)
    engine = create_async_engine(config.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = (await session.execute(select(Project))).scalar_one()
        session.add(
            SourceItem(
                project_id=project.id,
                provider=Provider.GITHUB,
                external_id="test:source:1",
                kind=SourceItemKind.TASK_CHANGE,
                title="GitHub issue #1",
                body="TODO: 정리 버튼을 추가한다.",
                occurred_at=project.created_at,
            )
        )
        await session.commit()
    await engine.dispose()


async def count_briefs(tmp_path):
    config = cli.load_or_default_config(tmp_path)
    engine = create_async_engine(config.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        count = len(list((await session.execute(select(BriefRevision))).scalars().all()))
    await engine.dispose()
    return count
