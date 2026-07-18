from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from threading import Timer
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openbrief.briefs.service import build_daily_revision
from openbrief.config import Settings
from openbrief.integrations.discord import poll_discord_integration
from openbrief.integrations.figma import sync_figma_integration
from openbrief.integrations.github import sync_github_integration
from openbrief.integrations.notion import sync_notion_integration
from openbrief.local_secrets import (
    get_or_create_secret,
    get_secret,
    migrate_legacy_secret,
    set_secret,
)
from openbrief.models import (
    Base,
    Integration,
    Project,
    ProjectMember,
    Provider,
    SourceItem,
    Workspace,
)
from openbrief.security import CredentialCipher

HOME_ENV = "OPENBRIEF_HOME"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
TOKEN_ENCRYPTION_SECRET = "token_encryption_key"
AI_SUMMARIZER_SECRET = "ai_summarizer_api_key"


@dataclass(frozen=True)
class LocalConfig:
    home: Path
    config_path: Path
    database_url: str
    host: str
    port: int
    open_browser: bool
    log_path: Path
    pid_path: Path
    run_path: Path
    token_encryption_key: str
    ai_summarizer_url: str | None
    ai_summarizer_api_key: str | None
    ai_summarizer_model: str

    @property
    def dashboard_url(self) -> str:
        return f"http://{self.host}:{self.port}/dashboard"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openbrief",
        description="OpenBrief local app launcher",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create local OpenBrief config and DB")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config")
    init_parser.add_argument("--home", type=Path, help="Override OpenBrief local app directory")
    init_parser.set_defaults(func=init_command)

    start_parser = subparsers.add_parser("start", help="Start the local OpenBrief web app")
    start_parser.add_argument("--daemon", action="store_true", help="Run in the background")
    start_parser.add_argument("--host", default=None, help="Host to bind")
    start_parser.add_argument("--port", type=int, default=None, help="Port to bind")
    start_parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    start_parser.add_argument("--home", type=Path, help="Override OpenBrief local app directory")
    start_parser.set_defaults(func=start_command)

    setup_parser = subparsers.add_parser("setup", help="Create a local project and connect sources")
    setup_parser.add_argument("--home", type=Path, help="Override OpenBrief local app directory")
    setup_parser.add_argument(
        "--project",
        "--project-name",
        dest="project_name",
        default="OpenBrief Project",
    )
    setup_parser.add_argument("--description", default="")
    setup_parser.add_argument(
        "--member",
        action="append",
        default=[],
        help="Advanced: approver as Name:email. Defaults to a local owner.",
    )
    setup_parser.add_argument("--figma-file-url", default=None)
    setup_parser.add_argument("--figma-token", default=None)
    setup_parser.add_argument("--notion-page-url", action="append", default=[])
    setup_parser.add_argument("--notion-token", default=None)
    setup_parser.add_argument("--discord-channel-id", default=None)
    setup_parser.add_argument("--discord-bot-token", default=None)
    setup_parser.add_argument("--github-repo", default=None)
    setup_parser.add_argument("--github-token", default=None)
    setup_parser.add_argument("--openai-api-key", default=None)
    setup_parser.add_argument(
        "--ai-url",
        default="https://api.openai.com/v1/chat/completions",
        help="OpenAI-compatible chat/completions endpoint",
    )
    setup_parser.add_argument("--ai-model", default="gpt-4.1-mini")
    setup_parser.set_defaults(func=setup_command)

    auth_parser = subparsers.add_parser("auth", help="Store provider/API tokens securely")
    auth_parser.add_argument(
        "provider",
        choices=["openai", "figma", "notion", "discord", "github"],
        help="Provider to authenticate",
    )
    auth_parser.add_argument("--home", type=Path, help="Override OpenBrief local app directory")
    auth_parser.add_argument("--project-id", default=None, help="Only update one project UUID")
    auth_parser.add_argument("--token", default=None, help="Token value. Prefer prompt input.")
    auth_parser.add_argument(
        "--ai-url",
        default="https://api.openai.com/v1/chat/completions",
        help="OpenAI-compatible chat/completions endpoint",
    )
    auth_parser.add_argument("--ai-model", default="gpt-4.1-mini")
    auth_parser.set_defaults(func=auth_command)

    sync_parser = subparsers.add_parser("sync", help="Poll connected sources into OpenBrief")
    sync_parser.add_argument("--home", type=Path, help="Override OpenBrief local app directory")
    sync_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in Provider],
        help="Only sync one provider",
    )
    sync_parser.add_argument("--project-id", default=None, help="Only sync one project UUID")
    sync_parser.add_argument("--brief", action="store_true", help="Generate a brief after sync")
    sync_parser.set_defaults(func=sync_command)

    brief_parser = subparsers.add_parser("brief", help="Generate a brief from collected sources")
    brief_parser.add_argument("--home", type=Path, help="Override OpenBrief local app directory")
    brief_parser.add_argument("--project-id", default=None, help="Project UUID")
    brief_parser.set_defaults(func=brief_command)

    stop_parser = subparsers.add_parser("stop", help="Stop a background OpenBrief process")
    stop_parser.add_argument("--home", type=Path, help="Override OpenBrief local app directory")
    stop_parser.set_defaults(func=stop_command)

    status_parser = subparsers.add_parser("status", help="Show local OpenBrief process status")
    status_parser.add_argument("--home", type=Path, help="Override OpenBrief local app directory")
    status_parser.set_defaults(func=status_command)

    serve_parser = subparsers.add_parser("_serve", help=argparse.SUPPRESS)
    serve_parser.add_argument("--home", type=Path, required=True)
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--no-browser", action="store_true")
    serve_parser.set_defaults(func=serve_command)

    return parser


def init_command(args: argparse.Namespace) -> int:
    config = ensure_initialized(home_arg=args.home, force=args.force)
    print(f"OpenBrief local app initialized at {config.home}")
    print(f"Config: {config.config_path}")
    print(f"Dashboard: {config.dashboard_url}")
    return 0


def start_command(args: argparse.Namespace) -> int:
    config = ensure_initialized(home_arg=args.home, force=False)
    config = with_overrides(
        config,
        host=args.host,
        port=args.port,
        open_browser=False if args.no_browser else None,
    )

    if is_running(config.pid_path):
        pid = config.pid_path.read_text(encoding="utf-8").strip()
        runtime_url = runtime_dashboard_url(config) or config.dashboard_url
        print(f"OpenBrief is already running with PID {pid}")
        print(f"Dashboard: {runtime_url}")
        return 0

    if args.daemon:
        return start_daemon(config)

    return run_server(config)


def stop_command(args: argparse.Namespace) -> int:
    config = load_or_default_config(home_arg=args.home)
    if not config.pid_path.exists():
        print("OpenBrief is not running.")
        return 0

    pid_text = config.pid_path.read_text(encoding="utf-8").strip()
    if not pid_text.isdigit():
        config.pid_path.unlink(missing_ok=True)
        print("Removed invalid OpenBrief PID file.")
        return 0

    pid = int(pid_text)
    if not process_exists(pid):
        config.pid_path.unlink(missing_ok=True)
        print("OpenBrief was not running. Removed stale PID file.")
        return 0

    os.kill(pid, signal.SIGTERM)
    config.pid_path.unlink(missing_ok=True)
    config.run_path.unlink(missing_ok=True)
    print(f"Stopped OpenBrief PID {pid}.")
    return 0


def status_command(args: argparse.Namespace) -> int:
    config = load_or_default_config(home_arg=args.home)
    if is_running(config.pid_path):
        pid = config.pid_path.read_text(encoding="utf-8").strip()
        runtime_url = runtime_dashboard_url(config) or config.dashboard_url
        print(f"OpenBrief is running with PID {pid}")
        print(f"Dashboard: {runtime_url}")
        return 0

    print("OpenBrief is not running.")
    print(f"Config: {config.config_path}")
    return 0


def setup_command(args: argparse.Namespace) -> int:
    config = ensure_initialized(home_arg=args.home, force=False)
    if args.openai_api_key:
        config = save_ai_settings(
            config,
            api_key=args.openai_api_key,
            url=args.ai_url,
            model=args.ai_model,
        )
    result = asyncio.run(configure_local_project(config, args))
    print(f"Configured project: {result['project_name']} ({result['project_id']})")
    for line in result["integrations"]:
        print(f"- {line}")
    if args.openai_api_key:
        print(f"- ai model={config.ai_summarizer_model}")
    print(f"Dashboard: {config.dashboard_url}/projects/{result['project_id']}")
    return 0


def auth_command(args: argparse.Namespace) -> int:
    config = ensure_initialized(home_arg=args.home, force=False)
    token = args.token or prompt_secret(f"{args.provider} token")
    if args.provider == "openai":
        config = save_ai_settings(
            config,
            api_key=token,
            url=args.ai_url,
            model=args.ai_model,
        )
        print(
            "Stored OpenAI-compatible API key in the OS credential store "
            f"for model {config.ai_summarizer_model}."
        )
        return 0

    updated = asyncio.run(
        update_provider_credentials(
            config,
            provider=Provider(args.provider),
            token=token,
            project_id_filter=args.project_id,
        )
    )
    if updated == 0:
        print(f"No {args.provider} integrations found. Run `openbrief setup` first.")
        return 0
    print(f"Stored {args.provider} token for {updated} integration(s).")
    return 0


def sync_command(args: argparse.Namespace) -> int:
    config = ensure_initialized(home_arg=args.home, force=False)
    results = asyncio.run(sync_local_integrations(config, args.provider, args.project_id))
    if not results:
        print("No matching integrations to sync. Run `openbrief setup` first.")
        return 0
    for result in results:
        if result.get("error"):
            print(f"{result['provider']} {result['name']}: error={result['error']}")
        else:
            print(
                f"{result['provider']} {result['name']}: "
                f"fetched={result['fetched']} stored={result['stored']} "
                f"duplicates={result['duplicates']} checkpoint={result['checkpoint']}"
            )
    if args.brief:
        brief_results = asyncio.run(generate_local_briefs(config, args.project_id))
        for brief in brief_results:
            print(
                f"brief {brief['project_name']}: "
                f"revision=v{brief['version']} status={brief['status']} "
                f"sources={brief['source_count']}"
            )
    return 0


def brief_command(args: argparse.Namespace) -> int:
    config = ensure_initialized(home_arg=args.home, force=False)
    results = asyncio.run(generate_local_briefs(config, args.project_id))
    if not results:
        print("No source items available. Run `openbrief sync` first.")
        return 0
    for result in results:
        print(
            f"brief {result['project_name']}: "
            f"revision=v{result['version']} status={result['status']} "
            f"sources={result['source_count']}"
        )
        print(f"Dashboard: {config.dashboard_url}/projects/{result['project_id']}")
    return 0


def serve_command(args: argparse.Namespace) -> int:
    config = ensure_initialized(home_arg=args.home, force=False)
    config = with_overrides(
        config,
        host=args.host,
        port=args.port,
        open_browser=False if args.no_browser else None,
    )
    return run_server(config)


def start_daemon(config: LocalConfig) -> int:
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = config.log_path.open("ab")
    command = [
        sys.executable,
        "-m",
        "openbrief.cli",
        "_serve",
        "--home",
        str(config.home),
        "--host",
        config.host,
        "--port",
        str(config.port),
    ]
    if not config.open_browser:
        command.append("--no-browser")

    process = subprocess.Popen(  # noqa: S603
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    time.sleep(0.5)
    if process.poll() is not None:
        print(f"OpenBrief failed to start. Check log: {config.log_path}")
        return process.returncode or 1

    print(f"Started OpenBrief in the background with PID {process.pid}")
    print(f"Dashboard: {config.dashboard_url}")
    print(f"Log: {config.log_path}")
    return 0


def run_server(config: LocalConfig) -> int:
    apply_runtime_env(config)
    config.pid_path.parent.mkdir(parents=True, exist_ok=True)
    config.pid_path.write_text(str(os.getpid()), encoding="utf-8")
    write_runtime(config)

    if config.open_browser:
        Timer(1.0, webbrowser.open, args=(config.dashboard_url,)).start()

    try:
        import uvicorn

        uvicorn.run("openbrief.main:app", host=config.host, port=config.port)
    finally:
        if config.pid_path.exists() and config.pid_path.read_text(encoding="utf-8") == str(
            os.getpid()
        ):
            config.pid_path.unlink(missing_ok=True)
            config.run_path.unlink(missing_ok=True)
    return 0


def ensure_initialized(home_arg: Path | None, force: bool) -> LocalConfig:
    config = load_or_default_config(home_arg=home_arg)
    config.home.mkdir(parents=True, exist_ok=True)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)

    if force or not config.config_path.exists() or config_needs_migration(config.config_path):
        write_config(config)
    asyncio.run(create_database(config.database_url))
    return config


async def create_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def configure_local_project(config: LocalConfig, args: argparse.Namespace) -> dict:
    factory, engine = session_factory(config)
    integrations: list[str] = []
    try:
        async with factory() as session:
            workspace = await first_workspace(session)
            if workspace is None:
                workspace = Workspace(name="OpenBrief Local")
                session.add(workspace)
                await session.flush()

            project = Project(
                workspace_id=workspace.id,
                name=args.project_name,
                description=args.description or "Local OpenBrief project",
            )
            session.add(project)
            await session.flush()

            members = parse_members(args.member)
            if not members:
                members = [("Owner", "owner@openbrief.local")]
            for display_name, email in members:
                session.add(
                    ProjectMember(
                        project_id=project.id,
                        display_name=display_name,
                        email=email,
                    )
                )

            if args.figma_file_url:
                file_key = parse_figma_file_key(args.figma_file_url)
                await add_local_integration(
                    session,
                    config,
                    project,
                    Provider.FIGMA,
                    external_id=file_key,
                    name=f"Figma {file_key}",
                    credentials={"access_token": args.figma_token} if args.figma_token else None,
                    config_data={"file_key": file_key},
                )
                integrations.append(f"figma file={file_key}")

            if args.notion_page_url:
                page_ids = [parse_notion_page_id(value) for value in args.notion_page_url]
                await add_local_integration(
                    session,
                    config,
                    project,
                    Provider.NOTION,
                    external_id=page_ids[0],
                    name="Notion pages",
                    credentials={"access_token": args.notion_token} if args.notion_token else None,
                    config_data={"page_ids": page_ids},
                )
                integrations.append(f"notion pages={','.join(page_ids)}")

            if args.discord_channel_id:
                await add_local_integration(
                    session,
                    config,
                    project,
                    Provider.DISCORD,
                    external_id=args.discord_channel_id,
                    name=f"Discord {args.discord_channel_id}",
                    credentials={"bot_token": args.discord_bot_token}
                    if args.discord_bot_token
                    else None,
                    config_data={"channel_id": args.discord_channel_id},
                )
                integrations.append(f"discord channel={args.discord_channel_id}")

            if args.github_repo:
                repository = parse_github_repo(args.github_repo)
                await add_local_integration(
                    session,
                    config,
                    project,
                    Provider.GITHUB,
                    external_id=repository,
                    name=f"GitHub {repository}",
                    credentials={"access_token": args.github_token} if args.github_token else None,
                    config_data={"repository": repository},
                )
                integrations.append(f"github repo={repository}")

            await session.commit()
            return {
                "project_id": str(project.id),
                "project_name": project.name,
                "integrations": integrations or ["no sources connected yet"],
            }
    finally:
        await engine.dispose()


async def add_local_integration(
    session: AsyncSession,
    config: LocalConfig,
    project: Project,
    provider: Provider,
    *,
    external_id: str,
    name: str,
    credentials: dict | None,
    config_data: dict,
) -> None:
    encrypted_credentials = None
    if credentials:
        cipher = CredentialCipher(config.token_encryption_key)
        encrypted_credentials = cipher.encrypt(json.dumps(credentials))
    session.add(
        Integration(
            project_id=project.id,
            provider=provider,
            external_id=external_id,
            name=name,
            encrypted_credentials=encrypted_credentials,
            config=config_data,
        )
    )


async def update_provider_credentials(
    config: LocalConfig,
    *,
    provider: Provider,
    token: str,
    project_id_filter: str | None,
) -> int:
    factory, engine = session_factory(config)
    try:
        async with factory() as session:
            query = select(Integration).where(Integration.provider == provider)
            if project_id_filter:
                query = query.where(Integration.project_id == uuid.UUID(project_id_filter))
            rows = await session.execute(query)
            integrations = list(rows.scalars().all())
            if not integrations:
                return 0

            credential_key = credential_key_for_provider(provider)
            cipher = CredentialCipher(config.token_encryption_key)
            for integration in integrations:
                integration.encrypted_credentials = cipher.encrypt(
                    json.dumps({credential_key: token})
                )
            await session.commit()
            return len(integrations)
    finally:
        await engine.dispose()


def credential_key_for_provider(provider: Provider) -> str:
    if provider == Provider.DISCORD:
        return "bot_token"
    return "access_token"


async def sync_local_integrations(
    config: LocalConfig,
    provider_filter: str | None,
    project_id_filter: str | None,
) -> list[dict]:
    factory, engine = session_factory(config)
    settings = local_settings(config)
    try:
        async with factory() as session:
            query = select(Integration).order_by(Integration.created_at.asc())
            if provider_filter:
                query = query.where(Integration.provider == Provider(provider_filter))
            if project_id_filter:
                query = query.where(Integration.project_id == uuid.UUID(project_id_filter))
            rows = await session.execute(query)
            integrations = list(rows.scalars().all())

            results: list[dict] = []
            for integration in integrations:
                try:
                    result = await sync_one_integration(session, integration, settings)
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc)}
                results.append(
                    {
                        "provider": integration.provider.value,
                        "name": integration.name,
                        **result,
                    }
                )
            return results
    finally:
        await engine.dispose()


async def generate_local_briefs(
    config: LocalConfig,
    project_id_filter: str | None,
) -> list[dict]:
    factory, engine = session_factory(config)
    settings = local_settings(config)
    try:
        async with factory() as session:
            project_query = select(Project).order_by(Project.created_at.asc())
            if project_id_filter:
                project_query = project_query.where(Project.id == uuid.UUID(project_id_filter))
            project_rows = await session.execute(project_query)
            projects = list(project_rows.scalars().all())

            results: list[dict] = []
            for project in projects:
                source_rows = await session.execute(
                    select(SourceItem)
                    .where(SourceItem.project_id == project.id)
                    .order_by(SourceItem.occurred_at.desc())
                    .limit(200)
                )
                source_items = list(source_rows.scalars().all())
                if not source_items:
                    continue
                revision = await build_daily_revision(
                    session,
                    project.id,
                    source_items,
                    created_by="local-cli",
                    settings=settings,
                )
                results.append(
                    {
                        "project_id": str(project.id),
                        "project_name": project.name,
                        "version": revision.version,
                        "status": revision.status.value,
                        "source_count": len(source_items),
                    }
                )
            return results
    finally:
        await engine.dispose()


async def sync_one_integration(
    session: AsyncSession,
    integration: Integration,
    settings: Settings,
) -> dict:
    if integration.provider == Provider.FIGMA:
        result = await sync_figma_integration(session, integration.id, settings)
        return {
            "fetched": result.fetched,
            "stored": result.stored,
            "duplicates": result.duplicates,
            "checkpoint": result.last_synced_at,
        }
    if integration.provider == Provider.NOTION:
        result = await sync_notion_integration(session, integration.id, settings)
        return {
            "fetched": result.fetched,
            "stored": result.stored,
            "duplicates": result.duplicates,
            "checkpoint": result.last_synced_at,
        }
    if integration.provider == Provider.DISCORD:
        result = await poll_discord_integration(session, integration.id, settings)
        return {
            "fetched": result.fetched,
            "stored": result.stored,
            "duplicates": result.duplicates,
            "checkpoint": result.last_message_id,
        }
    if integration.provider == Provider.GITHUB:
        result = await sync_github_integration(session, integration.id, settings)
        return {
            "fetched": result.fetched,
            "stored": result.stored,
            "duplicates": result.duplicates,
            "checkpoint": result.last_synced_at,
        }
    raise ValueError(f"{integration.provider.value} sync is not implemented")


def local_settings(config: LocalConfig) -> Settings:
    return Settings(
        token_encryption_key=config.token_encryption_key,
        ai_summarizer_url=config.ai_summarizer_url,
        ai_summarizer_api_key=config.ai_summarizer_api_key,
        ai_summarizer_model=config.ai_summarizer_model,
    )


def session_factory(config: LocalConfig) -> tuple[async_sessionmaker[AsyncSession], Any]:
    engine = create_async_engine(config.database_url)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def first_workspace(session: AsyncSession) -> Workspace | None:
    result = await session.execute(select(Workspace).order_by(Workspace.created_at.asc()).limit(1))
    return result.scalar_one_or_none()


def load_or_default_config(home_arg: Path | None) -> LocalConfig:
    home = local_home(home_arg)
    config_path = home / "config.toml"
    default = default_config(home)
    if not config_path.exists():
        return default

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    server = data.get("server", {})
    database = data.get("database", {})
    app = data.get("app", {})
    secrets = data.get("secrets", {})
    ai = data.get("ai", {})
    legacy_token_encryption_key = optional_string(secrets.get(TOKEN_ENCRYPTION_SECRET))
    legacy_ai_summarizer_api_key = optional_string(secrets.get(AI_SUMMARIZER_SECRET))
    migrate_legacy_secret(home, TOKEN_ENCRYPTION_SECRET, legacy_token_encryption_key)
    migrate_legacy_secret(home, AI_SUMMARIZER_SECRET, legacy_ai_summarizer_api_key)
    return LocalConfig(
        home=home,
        config_path=config_path,
        database_url=str(database.get("url", default.database_url)),
        host=str(server.get("host", default.host)),
        port=int(server.get("port", default.port)),
        open_browser=bool(app.get("open_browser", default.open_browser)),
        log_path=Path(app.get("log_path", default.log_path)).expanduser(),
        pid_path=Path(app.get("pid_path", default.pid_path)).expanduser(),
        run_path=Path(app.get("run_path", default.run_path)).expanduser(),
        token_encryption_key=get_or_create_secret(
            home,
            TOKEN_ENCRYPTION_SECRET,
            lambda: legacy_token_encryption_key or Fernet.generate_key().decode(),
        ),
        ai_summarizer_url=optional_string(ai.get("summarizer_url", default.ai_summarizer_url)),
        ai_summarizer_api_key=get_secret(home, AI_SUMMARIZER_SECRET),
        ai_summarizer_model=str(ai.get("model", default.ai_summarizer_model)),
    )


def default_config(home: Path) -> LocalConfig:
    database_path = home / "openbrief.db"
    return LocalConfig(
        home=home,
        config_path=home / "config.toml",
        database_url=sqlite_url(database_path),
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        open_browser=True,
        log_path=home / "logs" / "openbrief.log",
        pid_path=home / "openbrief.pid",
        run_path=home / "run.json",
        token_encryption_key=get_or_create_secret(
            home,
            TOKEN_ENCRYPTION_SECRET,
            lambda: Fernet.generate_key().decode(),
        ),
        ai_summarizer_url=None,
        ai_summarizer_api_key=get_secret(home, AI_SUMMARIZER_SECRET),
        ai_summarizer_model="gpt-4.1-mini",
    )


def with_overrides(
    config: LocalConfig,
    *,
    host: str | None,
    port: int | None,
    open_browser: bool | None,
) -> LocalConfig:
    return LocalConfig(
        home=config.home,
        config_path=config.config_path,
        database_url=config.database_url,
        host=host or config.host,
        port=port or config.port,
        open_browser=config.open_browser if open_browser is None else open_browser,
        log_path=config.log_path,
        pid_path=config.pid_path,
        run_path=config.run_path,
        token_encryption_key=config.token_encryption_key,
        ai_summarizer_url=config.ai_summarizer_url,
        ai_summarizer_api_key=config.ai_summarizer_api_key,
        ai_summarizer_model=config.ai_summarizer_model,
    )


def write_config(config: LocalConfig) -> None:
    config.config_path.write_text(
        f"""# OpenBrief local app config

[server]
host = "{config.host}"
port = {config.port}

[database]
url = "{config.database_url}"

[app]
open_browser = {str(config.open_browser).lower()}
log_path = "{config.log_path}"
pid_path = "{config.pid_path}"
run_path = "{config.run_path}"

[ai]
summarizer_url = {toml_string_or_none(config.ai_summarizer_url)}
model = "{config.ai_summarizer_model}"
""",
        encoding="utf-8",
    )


def save_ai_settings(
    config: LocalConfig,
    *,
    api_key: str,
    url: str,
    model: str,
) -> LocalConfig:
    set_secret(config.home, AI_SUMMARIZER_SECRET, api_key)
    updated = LocalConfig(
        home=config.home,
        config_path=config.config_path,
        database_url=config.database_url,
        host=config.host,
        port=config.port,
        open_browser=config.open_browser,
        log_path=config.log_path,
        pid_path=config.pid_path,
        run_path=config.run_path,
        token_encryption_key=config.token_encryption_key,
        ai_summarizer_url=url,
        ai_summarizer_api_key=get_secret(config.home, AI_SUMMARIZER_SECRET),
        ai_summarizer_model=model,
    )
    write_config(updated)
    return updated


def toml_string_or_none(value: str | None) -> str:
    if not value:
        return '""'
    return json.dumps(value)


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def config_needs_migration(path: Path) -> bool:
    if not path.exists():
        return False
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return bool(data.get("secrets")) or "ai" not in data


def apply_runtime_env(config: LocalConfig) -> None:
    os.environ.setdefault(HOME_ENV, str(config.home))
    os.environ.setdefault("DATABASE_URL", config.database_url)
    os.environ.setdefault("ENVIRONMENT", "local")
    os.environ.setdefault("TOKEN_ENCRYPTION_KEY", config.token_encryption_key)
    if config.ai_summarizer_url:
        os.environ.setdefault("AI_SUMMARIZER_URL", config.ai_summarizer_url)
    if config.ai_summarizer_api_key:
        os.environ.setdefault("AI_SUMMARIZER_API_KEY", config.ai_summarizer_api_key)
    os.environ.setdefault("AI_SUMMARIZER_MODEL", config.ai_summarizer_model)


def local_home(home_arg: Path | None = None) -> Path:
    if home_arg is not None:
        return home_arg.expanduser().resolve()
    if env_home := os.environ.get(HOME_ENV):
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".openbrief").resolve()


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.expanduser().resolve()}"


def parse_members(values: list[str]) -> list[tuple[str, str]]:
    members: list[tuple[str, str]] = []
    for value in values:
        if ":" not in value:
            raise ValueError("--member must use Name:email")
        name, email = value.split(":", 1)
        members.append((name.strip(), email.strip()))
    return members


def parse_figma_file_key(value: str) -> str:
    marker = "/file/"
    if marker in value:
        return value.split(marker, 1)[1].split("/", 1)[0].split("?", 1)[0]
    marker = "/design/"
    if marker in value:
        return value.split(marker, 1)[1].split("/", 1)[0].split("?", 1)[0]
    return value.strip()


def parse_notion_page_id(value: str) -> str:
    normalized = value.strip().split("?", 1)[0].rstrip("/")
    tail = normalized.rsplit("/", 1)[-1]
    candidate = tail.rsplit("-", 1)[-1]
    return candidate.replace("-", "")


def parse_github_repo(value: str) -> str:
    normalized = value.strip().removeprefix("https://github.com/").removesuffix(".git")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) < 2:
        raise ValueError("--github-repo must be owner/repo or a GitHub repository URL")
    return f"{parts[0]}/{parts[1]}"


def prompt_secret(label: str) -> str:
    token = getpass.getpass(f"{label}: ").strip()
    if not token:
        raise ValueError(f"{label} is required")
    return token


def write_runtime(config: LocalConfig) -> None:
    config.run_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": config.host,
                "port": config.port,
                "dashboard_url": config.dashboard_url,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def runtime_dashboard_url(config: LocalConfig) -> str | None:
    if not config.run_path.exists():
        return None
    try:
        data = json.loads(config.run_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data.get("dashboard_url")


def is_running(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    pid_text = pid_path.read_text(encoding="utf-8").strip()
    return pid_text.isdigit() and process_exists(int(pid_text))


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    raise SystemExit(main())
