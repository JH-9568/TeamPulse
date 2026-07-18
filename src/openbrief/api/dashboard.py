import html
import uuid
from collections import Counter
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openbrief.briefs.service import approval_state, build_daily_revision
from openbrief.cli import (
    AI_SUMMARIZER_SECRET,
    HOME_ENV,
    parse_figma_file_key,
    parse_github_repo,
    parse_notion_page_id,
)
from openbrief.config import Settings, get_settings
from openbrief.db import get_session
from openbrief.integrations.discord import poll_discord_integration
from openbrief.integrations.figma import sync_figma_integration
from openbrief.integrations.github import sync_github_integration
from openbrief.integrations.notion import sync_notion_integration
from openbrief.local_secrets import set_secret
from openbrief.models import (
    BriefApproval,
    BriefRevision,
    Integration,
    IntegrationStatus,
    NotificationDelivery,
    Project,
    ProjectMember,
    Provider,
    SourceItem,
    Workspace,
)
from openbrief.schemas import ApprovalRead
from openbrief.security import CredentialCipher
from openbrief.sources.service import list_source_items

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse)
async def dashboard_home(session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    result = await session.execute(select(Project).order_by(Project.created_at.desc()))
    projects = list(result.scalars().all())
    return HTMLResponse(render_dashboard_home(projects))


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_dashboard(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    brief = await latest_brief(session, project_id)
    state = await approval_state(session, brief) if brief else None
    members = await active_members(session, project_id)
    source_items = await latest_source_items(session, project_id)
    integrations = await project_integrations(session, project_id)
    return HTMLResponse(
        render_project_dashboard(project, brief, state, members, source_items, integrations)
    )


@router.post("/setup/project")
async def dashboard_create_project(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    form = await read_urlencoded_form(request)
    workspace = await first_workspace(session)
    if workspace is None:
        workspace = Workspace(name="OpenBrief Local")
        session.add(workspace)
        await session.flush()

    project = Project(
        workspace_id=workspace.id,
        name=form.get("name") or "Untitled Project",
        description=form.get("description") or "Local OpenBrief project",
    )
    session.add(project)
    await session.flush()
    session.add(
        ProjectMember(
            project_id=project.id,
            display_name="Owner",
            email="owner@openbrief.local",
            role="owner",
        )
    )
    await session.commit()
    return redirect(f"/dashboard/projects/{project.id}")


@router.post("/projects/{project_id}/integrations")
async def dashboard_add_integration(
    project_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    form = await read_urlencoded_form(request)
    provider = Provider(form.get("provider") or Provider.GITHUB)
    external_id, name, config = integration_config_from_form(provider, form)
    credentials = credentials_from_form(provider, form)
    encrypted_credentials = None
    if credentials:
        if settings.token_encryption_key is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "TOKEN_ENCRYPTION_KEY is required")
        cipher = CredentialCipher(settings.token_encryption_key.get_secret_value())
        encrypted_credentials = cipher.encrypt(json_dumps(credentials))
    integration = Integration(
        project_id=project.id,
        provider=provider,
        external_id=external_id,
        name=name,
        encrypted_credentials=encrypted_credentials,
        config=config,
    )
    session.add(integration)
    await session.commit()
    return redirect(f"/dashboard/projects/{project.id}#connections")


@router.post("/projects/{project_id}/settings/ai")
async def dashboard_save_ai_settings(
    project_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    form = await read_urlencoded_form(request)
    api_key = form.get("api_key")
    if api_key:
        home = local_home_from_env()
        set_secret(home, AI_SUMMARIZER_SECRET, api_key)
        # Make the key available to the already running local server process.
        import os

        os.environ["AI_SUMMARIZER_API_KEY"] = api_key
    if model := form.get("model"):
        import os

        os.environ["AI_SUMMARIZER_MODEL"] = model
    if url := form.get("url"):
        import os

        os.environ["AI_SUMMARIZER_URL"] = url
    return redirect(f"/dashboard/projects/{project_id}#settings")


@router.post("/projects/{project_id}/sync")
async def dashboard_sync_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    integrations = await project_integrations(session, project_id)
    if not integrations:
        return redirect(f"/dashboard/projects/{project_id}#connections")
    for integration in integrations:
        try:
            await sync_one_integration(session, integration, settings)
            integration.status = IntegrationStatus.ACTIVE
            integration.config = {
                **(integration.config or {}),
                "last_error": None,
            }
        except Exception as exc:  # noqa: BLE001
            integration.status = IntegrationStatus.ERROR
            integration.config = {
                **(integration.config or {}),
                "last_error": str(exc),
            }
    await session.commit()
    return redirect(f"/dashboard/projects/{project_id}#connections")


@router.post("/projects/{project_id}/brief")
async def dashboard_generate_brief(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    source_items = await list_source_items(session, project_id, None, None)
    if source_items:
        await build_daily_revision(session, project_id, source_items, settings=settings)
    return redirect(f"/dashboard/projects/{project_id}#brief")


@router.post("/projects/{project_id}/delete")
async def dashboard_delete_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    await delete_project_data(session, project_id)
    await session.delete(project)
    await session.commit()
    return redirect("/dashboard")


@router.post("/projects/{project_id}/sources/delete")
async def dashboard_delete_project_sources(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    for item in await all_source_items(session, project_id):
        await session.delete(item)
    await session.commit()
    return redirect(f"/dashboard/projects/{project_id}#sources")


@router.post("/projects/{project_id}/integrations/{integration_id}/token/remove")
async def dashboard_remove_integration_token(
    project_id: uuid.UUID,
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    integration = await session.get(Integration, integration_id)
    if integration is None or integration.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    integration.encrypted_credentials = None
    await session.commit()
    return redirect(f"/dashboard/projects/{project_id}#connections")


def render_dashboard_home(projects: list[Project]) -> str:
    project_rows = "\n".join(
        "<article class='project-card'>"
        "<div>"
        f"<p class='eyebrow'>{'active' if project.active else 'inactive'}</p>"
        f"<h2><a href='/dashboard/projects/{project.id}'>{html.escape(project.name)}</a></h2>"
        f"<p>{html.escape(project.description or '')}</p>"
        "</div>"
        "<span class='card-arrow'>→</span>"
        "</article>"
        for project in projects
    )
    if not project_rows:
        project_rows = """
        <div class='empty-state'>
          <h3>아직 프로젝트가 없습니다.</h3>
          <p>아래 온보딩 폼에서 프로젝트를 만들고 GitHub부터 연결해보세요.</p>
        </div>
        """
    body = f"""
    <section class="hero">
      <div>
        <p class="eyebrow">OpenBrief Local</p>
        <h1>흩어진 프로젝트 맥락을 로컬에서 정리하세요.</h1>
        <p class="hero-copy">
          Figma, Notion, Discord, GitHub, Slack에 흩어진 회의·시안·업무 변경을
          읽기 전용으로 모아 근거 기반 브리프로 정리합니다.
        </p>
      </div>
      <div class="hero-panel">
        <span>오늘의 정리</span>
        <strong>{len(projects)}</strong>
        <small>active project candidates</small>
      </div>
    </section>
    <section class="content-section">
      <div class="section-heading">
        <p class="eyebrow">Projects</p>
        <h2>진행 중인 프로젝트</h2>
      </div>
      <div class="project-grid">{project_rows}</div>
    </section>
    <section class="content-section" id="setup">
      <div class="section-heading">
        <p class="eyebrow">First run</p>
        <h2>첫 프로젝트 만들기</h2>
      </div>
      <form class="setup-panel" method="post" action="/dashboard/setup/project">
        <label>프로젝트 이름
          <input name="name" placeholder="예: Brand Renewal Sprint" required>
        </label>
        <label>설명
          <input name="description" placeholder="디자인, 회의록, 할 일을 모아 정리할 프로젝트">
        </label>
        <button type="submit">프로젝트 만들기</button>
      </form>
    </section>
    """
    return html_page("OpenBrief", body)


async def latest_brief(session: AsyncSession, project_id: uuid.UUID) -> BriefRevision | None:
    result = await session.execute(
        select(BriefRevision)
        .where(BriefRevision.project_id == project_id)
        .order_by(BriefRevision.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_source_items(session: AsyncSession, project_id: uuid.UUID) -> list[SourceItem]:
    result = await session.execute(
        select(SourceItem)
        .where(SourceItem.project_id == project_id)
        .order_by(SourceItem.occurred_at.desc())
        .limit(25)
    )
    return list(result.scalars().all())


async def active_members(session: AsyncSession, project_id: uuid.UUID) -> list[ProjectMember]:
    result = await session.execute(
        select(ProjectMember)
        .where(ProjectMember.project_id == project_id, ProjectMember.active.is_(True))
        .order_by(ProjectMember.display_name.asc())
    )
    return list(result.scalars().all())


async def first_workspace(session: AsyncSession) -> Workspace | None:
    result = await session.execute(select(Workspace).order_by(Workspace.created_at.asc()).limit(1))
    return result.scalar_one_or_none()


async def project_integrations(
    session: AsyncSession, project_id: uuid.UUID
) -> list[Integration]:
    result = await session.execute(
        select(Integration)
        .where(Integration.project_id == project_id)
        .order_by(Integration.created_at.asc())
    )
    return list(result.scalars().all())


async def all_source_items(session: AsyncSession, project_id: uuid.UUID) -> list[SourceItem]:
    result = await session.execute(select(SourceItem).where(SourceItem.project_id == project_id))
    return list(result.scalars().all())


async def delete_project_data(session: AsyncSession, project_id: uuid.UUID) -> None:
    brief_rows = await session.execute(
        select(BriefRevision).where(BriefRevision.project_id == project_id)
    )
    briefs = list(brief_rows.scalars().all())
    for brief in briefs:
        approval_rows = await session.execute(
            select(BriefApproval).where(BriefApproval.brief_revision_id == brief.id)
        )
        for approval in approval_rows.scalars().all():
            await session.delete(approval)
        delivery_rows = await session.execute(
            select(NotificationDelivery).where(NotificationDelivery.brief_revision_id == brief.id)
        )
        for delivery in delivery_rows.scalars().all():
            await session.delete(delivery)
        await session.delete(brief)

    for item in await all_source_items(session, project_id):
        await session.delete(item)
    for integration in await project_integrations(session, project_id):
        await session.delete(integration)
    member_rows = await session.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    )
    for member in member_rows.scalars().all():
        await session.delete(member)


async def sync_one_integration(
    session: AsyncSession,
    integration: Integration,
    settings: Settings,
) -> None:
    if integration.provider == Provider.FIGMA:
        await sync_figma_integration(session, integration.id, settings)
        return
    if integration.provider == Provider.NOTION:
        await sync_notion_integration(session, integration.id, settings)
        return
    if integration.provider == Provider.DISCORD:
        await poll_discord_integration(session, integration.id, settings)
        return
    if integration.provider == Provider.GITHUB:
        await sync_github_integration(session, integration.id, settings)
        return
    raise ValueError(f"{integration.provider.value} sync is not implemented")


async def read_urlencoded_form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode()
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1].strip() for key, values in parsed.items()}


def integration_config_from_form(
    provider: Provider, form: dict[str, str]
) -> tuple[str, str, dict]:
    raw_target = form.get("target", "")
    if provider == Provider.GITHUB:
        repository = parse_github_repo(raw_target)
        return repository, f"GitHub {repository}", {"repository": repository}
    if provider == Provider.FIGMA:
        file_key = parse_figma_file_key(raw_target)
        return file_key, f"Figma {file_key}", {"file_key": file_key}
    if provider == Provider.NOTION:
        page_id = parse_notion_page_id(raw_target)
        return page_id, "Notion pages", {"page_ids": [page_id]}
    if provider == Provider.DISCORD:
        channel_id = raw_target
        return channel_id, f"Discord {channel_id}", {"channel_id": channel_id}
    raise ValueError(f"{provider.value} is not supported yet")


def credentials_from_form(provider: Provider, form: dict[str, str]) -> dict[str, str] | None:
    token = form.get("token")
    if not token:
        return None
    if provider == Provider.DISCORD:
        return {"bot_token": token}
    return {"access_token": token}


def json_dumps(value: dict[str, str]) -> str:
    import json

    return json.dumps(value)


def local_home_from_env():
    import os
    from pathlib import Path

    return Path(os.environ.get(HOME_ENV, "~/.openbrief")).expanduser().resolve()


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def render_project_dashboard(
    project: Project,
    brief: BriefRevision | None,
    state: ApprovalRead | None,
    members: list[ProjectMember],
    source_items: list[SourceItem],
    integrations: list[Integration],
) -> str:
    source_lookup = {str(item.id): item for item in source_items}
    brief_html = (
        render_brief(brief, state, members, source_lookup)
        if brief
        else render_empty_brief(project.id, bool(source_items))
    )
    sources_html = "\n".join(render_source_item(item) for item in source_items)
    if not sources_html:
        sources_html = (
            "<div class='empty-state'><h3>아직 수집된 원본 근거가 없습니다.</h3>"
            "<p>연결을 추가한 뒤 Sync now를 실행하세요.</p></div>"
        )
    provider_counts = Counter(item.provider.value for item in source_items)
    provider_pills = "\n".join(
        f"<span class='provider-pill provider-{html.escape(provider)}'>"
        f"{html.escape(provider)} <strong>{count}</strong></span>"
        for provider, count in sorted(provider_counts.items())
    )
    if not provider_pills:
        provider_pills = "<span class='provider-pill'>No sources</span>"
    connection_cards = render_connection_cards(project.id, integrations)
    next_actions = render_next_actions(
        project.id,
        integrations,
        bool(source_items),
        brief is not None,
    )
    setup_panel = render_project_setup_panel(project.id)
    reminder_panel = render_reminder_panel(project)
    approval_text = "No brief"
    if state is not None:
        approval_text = f"{state.approved_count}/{state.required_count} approvals"
    brief_status = html.escape(brief.status.value.replace("_", " ")) if brief else "not generated"
    body = f"""
    <nav class="topbar">
      <a class="brand" href="/dashboard">
        <span class="brand-mark">TP</span>
        <span>OpenBrief</span>
      </a>
      <div class="topbar-actions">
        <a class="ghost-button" href="/docs">API Docs</a>
        <a class="ghost-button" href="/dashboard">Projects</a>
      </div>
    </nav>

    <section class="project-hero">
      <div>
        <p class="eyebrow">Project cockpit</p>
        <h1>{html.escape(project.name)}</h1>
        <p class="hero-copy">{html.escape(project.description or "")}</p>
        <div class="provider-row">{provider_pills}</div>
        <div class="hero-actions">
          <form method="post" action="/dashboard/projects/{project.id}/sync">
            <button type="submit">Sync now</button>
          </form>
          <form method="post" action="/dashboard/projects/{project.id}/brief">
            <button class="secondary-button" type="submit">Generate brief</button>
          </form>
        </div>
      </div>
      <aside class="status-card">
        <p class="eyebrow">Current status</p>
        <strong>{html.escape(approval_text)}</strong>
        <span>{len(source_items)} source evidence items</span>
      </aside>
    </section>

    <section class="metric-grid">
      <article class="metric-card">
        <span>Brief revision</span>
        <strong>{f"v{brief.version}" if brief else "-"}</strong>
        <small>{brief_status}</small>
      </article>
      <article class="metric-card">
        <span>Reviewers</span>
        <strong>{len(members)}</strong>
        <small>local confirmation profile</small>
      </article>
      <article class="metric-card">
        <span>Evidence</span>
        <strong>{len(source_items)}</strong>
        <small>latest collected items</small>
      </article>
    </section>

    {next_actions}

    <section class="content-section" id="connections">
      <div class="section-heading">
        <p class="eyebrow">Connections</p>
        <h2>연결 상태와 진단</h2>
      </div>
      <div class="connection-grid">{connection_cards}</div>
    </section>

    <section class="content-section" id="setup">
      <div class="section-heading">
        <p class="eyebrow">Setup</p>
        <h2>웹에서 연결 추가</h2>
      </div>
      {setup_panel}
    </section>

    <section class="dashboard-layout">
      <div class="main-column">
        <div class="section-heading">
          <p class="eyebrow">AI Brief</p>
          <h2>Latest Brief · 검토할 프로젝트 정리</h2>
        </div>
        {brief_html}
      </div>
      <aside class="side-column">
        <div class="section-heading">
          <p class="eyebrow" id="sources">Source Evidence</p>
          <h2>수집된 원본 근거</h2>
        </div>
        <div class="source-list">{sources_html}</div>
      </aside>
    </section>

    <section class="content-section" id="settings">
      <div class="section-heading">
        <p class="eyebrow">Settings</p>
        <h2>AI·알림·데이터 관리</h2>
      </div>
      {reminder_panel}
      {render_ai_settings_panel(project.id)}
      {render_danger_zone(project.id)}
    </section>
    """
    return html_page(f"OpenBrief - {project.name}", body)


def render_next_actions(
    project_id: uuid.UUID,
    integrations: list[Integration],
    has_sources: bool,
    has_brief: bool,
) -> str:
    actions = []
    if not integrations:
        actions.append(("1", "GitHub부터 연결하기", f"/dashboard/projects/{project_id}#setup"))
    if integrations and not has_sources:
        actions.append(("2", "첫 sync 실행", f"/dashboard/projects/{project_id}#connections"))
    if has_sources and not has_brief:
        actions.append(("3", "브리프 생성하기", f"/dashboard/projects/{project_id}#brief"))
    actions.append(("4", "OpenAI key 추가하기", f"/dashboard/projects/{project_id}#settings"))
    items = "\n".join(
        f"<a class='action-card' href='{href}'>"
        f"<span>{step}</span><strong>{html.escape(label)}</strong>"
        "<small>다음 단계로 이동</small></a>"
        for step, label, href in actions[:4]
    )
    return f"""
    <section class="content-section">
      <div class="section-heading">
        <p class="eyebrow">Next actions</p>
        <h2>지금 해야 할 일</h2>
      </div>
      <div class="action-grid">{items}</div>
    </section>
    """


def render_connection_cards(project_id: uuid.UUID, integrations: list[Integration]) -> str:
    if not integrations:
        return (
            "<div class='empty-state'><h3>아직 연결된 도구가 없습니다.</h3>"
            "<p>GitHub 공개 저장소를 먼저 연결하면 토큰 없이도 테스트할 수 있습니다.</p></div>"
        )
    cards = []
    for integration in integrations:
        config = integration.config or {}
        last_error = config.get("last_error")
        checkpoint = config.get("last_synced_at") or config.get("last_message_id") or "not synced"
        token_status = (
            "token stored" if integration.encrypted_credentials else "token needed/optional"
        )
        status_label = "권한 확인 필요" if last_error else integration.status.value
        error_html = (
            f"<p class='error-text'>Last error: {html.escape(str(last_error))}</p>"
            if last_error
            else ""
        )
        remove_token_action = (
            f"/dashboard/projects/{project_id}/integrations/"
            f"{integration.id}/token/remove"
        )
        cards.append(
            f"""
            <article class="connection-card provider-{html.escape(integration.provider.value)}">
              <div class="connection-head">
                <div>
                  <p class="eyebrow">{html.escape(integration.provider.value)}</p>
                  <h3>{html.escape(integration.name)}</h3>
                </div>
                <span class="status-badge">{html.escape(status_label)}</span>
              </div>
              <p class="muted">Target: {html.escape(integration.external_id)}</p>
              <p class="muted">Credential: {html.escape(token_status)}</p>
              <p class="muted">Checkpoint: {html.escape(str(checkpoint))}</p>
              {error_html}
              <div class="toolbar">
                <form method="post" action="/dashboard/projects/{project_id}/sync">
                  <button type="submit">Sync now</button>
                </form>
                <form method="post" action="{remove_token_action}">
                  <button class="danger-button" type="submit">Remove token</button>
                </form>
              </div>
            </article>
            """
        )
    return "\n".join(cards)


def render_project_setup_panel(project_id: uuid.UUID) -> str:
    return f"""
    <form class="setup-panel" method="post" action="/dashboard/projects/{project_id}/integrations">
      <label>Provider
        <select name="provider">
          <option value="github">GitHub · owner/repo</option>
          <option value="figma">Figma · file URL</option>
          <option value="notion">Notion · page URL</option>
          <option value="discord">Discord · channel id</option>
        </select>
      </label>
      <label>대상 값
        <input name="target" placeholder="예: JH-9568/OpenBrief 또는 Figma/Notion URL" required>
      </label>
      <label>Token/API key
        <input name="token" type="password" placeholder="Public GitHub 테스트는 비워도 됩니다">
      </label>
      <button type="submit">연결 추가</button>
      <p class="muted">
        입력한 provider token은 로컬 DB에 암호화 저장됩니다.
        원본 서비스는 수정하지 않습니다.
      </p>
    </form>
    """


def render_ai_settings_panel(project_id: uuid.UUID) -> str:
    return f"""
    <form class="setup-panel" method="post" action="/dashboard/projects/{project_id}/settings/ai">
      <label>OpenAI-compatible API key
        <input name="api_key" type="password" placeholder="sk-...">
      </label>
      <label>Endpoint
        <input name="url" value="https://api.openai.com/v1/chat/completions">
      </label>
      <label>Model
        <input name="model" value="gpt-4.1-mini">
      </label>
      <button type="submit">AI 설정 저장</button>
      <p class="muted">
        API key는 OS credential store에 저장됩니다.
        config.toml에 평문 저장하지 않습니다.
      </p>
    </form>
    """


def render_reminder_panel(project: Project) -> str:
    channel = html.escape(project.daily_report_channel_id or "not configured")
    return f"""
    <article class="settings-card">
      <p class="eyebrow">Reminder</p>
      <h3>일일 브리프 알림</h3>
      <p class="muted">현재 Discord 알림 채널: {channel}</p>
      <p class="muted">
        웹에서 시간/채널을 수정하는 기능은 다음 단계입니다.
        지금은 연결 진단과 수동 발송부터 안정화합니다.
      </p>
    </article>
    """


def render_danger_zone(project_id: uuid.UUID) -> str:
    return f"""
    <article class="settings-card danger-zone">
      <p class="eyebrow">Danger zone</p>
      <h3>로컬 데이터 관리</h3>
      <p class="muted">
        삭제는 내 컴퓨터의 OpenBrief 로컬 DB에만 적용됩니다.
        원본 서비스는 수정하지 않습니다.
      </p>
      <div class="toolbar">
        <form method="post" action="/dashboard/projects/{project_id}/sources/delete">
          <button class="danger-button" type="submit">수집 데이터 삭제</button>
        </form>
        <form method="post" action="/dashboard/projects/{project_id}/delete">
          <button class="danger-button" type="submit">프로젝트 삭제</button>
        </form>
      </div>
    </article>
    """


def render_empty_brief(project_id: uuid.UUID, has_sources: bool) -> str:
    cta = (
        f"<form method='post' action='/dashboard/projects/{project_id}/brief'>"
        "<button type='submit'>Generate brief</button></form>"
        if has_sources
        else "<a class='ghost-button' href='#connections'>먼저 sync 실행하기</a>"
    )
    return f"""
    <article class="brief-card empty-brief" id="brief">
      <p class="eyebrow">AI Brief</p>
      <h3>아직 브리프가 없습니다.</h3>
      <p class="muted">
        Source evidence를 수집한 뒤 브리프를 생성하면 결정사항, 할 일,
        디자인 피드백, 일정 리스크를 나눠 볼 수 있습니다.
      </p>
      <div class="toolbar">{cta}</div>
    </article>
    """


def html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --surface: #ffffff;
      --surface-strong: #f8fafc;
      --text: #0f172a;
      --muted: #64748b;
      --line: #dbe3ef;
      --primary: #4f46e5;
      --primary-dark: #3730a3;
      --primary-soft: #eef2ff;
      --success: #059669;
      --warning: #d97706;
      --shadow: 0 24px 80px rgba(15, 23, 42, 0.10);
      --radius: 24px;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      min-width: 1024px;
      margin: 0;
      font-family:
        Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(79, 70, 229, 0.18), transparent 34rem),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 42%, #eef4fb 100%);
      line-height: 1.55;
      word-break: keep-all;
      overflow-wrap: normal;
    }}
    a {{
      color: inherit;
      text-decoration: none;
    }}
    main {{
      width: min(1280px, calc(100vw - 56px));
      margin: 0 auto;
      padding: 28px 0 56px;
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 28px;
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-weight: 800;
      letter-spacing: -0.03em;
    }}
    .brand-mark {{
      display: grid;
      place-items: center;
      width: 38px;
      height: 38px;
      border-radius: 14px;
      color: white;
      background: linear-gradient(135deg, var(--primary), #06b6d4);
      box-shadow: 0 14px 30px rgba(79, 70, 229, 0.25);
      font-size: 13px;
    }}
    .topbar-actions, .provider-row, .toolbar {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .ghost-button {{
      padding: 10px 14px;
      border: 1px solid rgba(79, 70, 229, 0.18);
      border-radius: 999px;
      color: var(--primary-dark);
      background: rgba(255, 255, 255, 0.72);
      font-size: 14px;
      font-weight: 700;
    }}
    .hero, .project-hero {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 24px;
      align-items: stretch;
      padding: 34px;
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 32px;
      background: rgba(255, 255, 255, 0.78);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }}
    .hero {{
      margin-top: 18px;
    }}
    .hero h1, .project-hero h1 {{
      max-width: 760px;
      margin: 8px 0 14px;
      font-size: clamp(42px, 6vw, 72px);
      line-height: 0.98;
      letter-spacing: -0.075em;
    }}
    .hero-copy {{
      max-width: 720px;
      margin: 0;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.75;
    }}
    .hero-panel, .status-card {{
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      min-height: 220px;
      padding: 24px;
      border-radius: var(--radius);
      color: white;
      background:
        linear-gradient(160deg, rgba(15, 23, 42, 0.92), rgba(79, 70, 229, 0.88)),
        radial-gradient(circle at top right, rgba(34, 211, 238, 0.42), transparent 14rem);
    }}
    .hero-panel strong, .status-card strong {{
      display: block;
      margin: 8px 0;
      font-size: 56px;
      line-height: 1;
      letter-spacing: -0.06em;
    }}
    .status-card strong {{
      font-size: 36px;
    }}
    .hero-panel span, .hero-panel small, .status-card span {{
      color: rgba(255, 255, 255, 0.72);
    }}
    .eyebrow {{
      margin: 0;
      color: var(--primary);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }}
    .content-section, .dashboard-layout, .metric-grid {{
      margin-top: 26px;
    }}
    .section-heading {{
      margin-bottom: 14px;
    }}
    .section-heading h2 {{
      margin: 4px 0 0;
      font-size: 24px;
      letter-spacing: -0.04em;
    }}
    .project-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .project-card {{
      display: flex;
      justify-content: space-between;
      min-height: 180px;
      padding: 24px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      box-shadow: 0 14px 44px rgba(15, 23, 42, 0.06);
    }}
    .project-card h2 {{
      margin: 8px 0;
      font-size: 28px;
      letter-spacing: -0.05em;
    }}
    .project-card p {{
      margin: 0;
      color: var(--muted);
    }}
    .card-arrow {{
      display: grid;
      place-items: center;
      flex: 0 0 auto;
      width: 42px;
      height: 42px;
      border-radius: 50%;
      background: var(--primary-soft);
      color: var(--primary);
      font-weight: 900;
    }}
    .provider-row {{
      margin-top: 22px;
    }}
    .provider-pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 9px 12px;
      border: 1px solid rgba(100, 116, 139, 0.16);
      border-radius: 999px;
      color: #334155;
      background: rgba(255, 255, 255, 0.72);
      font-size: 13px;
      font-weight: 800;
      text-transform: capitalize;
    }}
    .provider-pill strong {{
      display: grid;
      place-items: center;
      min-width: 22px;
      height: 22px;
      padding: 0 6px;
      border-radius: 999px;
      color: white;
      background: var(--text);
      font-size: 12px;
    }}
    .provider-figma strong {{ background: #a855f7; }}
    .provider-discord strong {{ background: #5865f2; }}
    .provider-notion strong {{ background: #111827; }}
    .provider-github strong {{ background: #0f172a; }}
    .provider-slack strong {{ background: #16a34a; }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
    }}
    .metric-card, .approval-card, .brief-card, .source-card,
    .connection-card, .settings-card, .setup-panel, .action-card {{
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.88);
      box-shadow: 0 14px 44px rgba(15, 23, 42, 0.06);
    }}
    .metric-card {{
      padding: 22px;
    }}
    .metric-card span, .metric-card small {{
      color: var(--muted);
      font-weight: 700;
    }}
    .metric-card strong {{
      display: block;
      margin: 6px 0;
      font-size: 34px;
      line-height: 1;
      letter-spacing: -0.06em;
    }}
    .dashboard-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 380px;
      gap: 22px;
      align-items: start;
    }}
    .main-column, .side-column {{
      min-width: 0;
    }}
    .brief-toolbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 14px;
      padding: 16px 18px;
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.82);
    }}
    .brief-toolbar p {{
      margin: 0;
    }}
    .status-badge, code {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 9px;
      border-radius: 999px;
      color: var(--primary-dark);
      background: var(--primary-soft);
      font-size: 12px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .approval-card {{
      margin-bottom: 16px;
      padding: 22px;
    }}
    .approval-header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .approval-header h3, .brief-card h3, .source-card h3 {{
      margin: 0;
      letter-spacing: -0.04em;
    }}
    .approval-progress {{
      position: relative;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: #e2e8f0;
    }}
    .approval-progress span {{
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--primary), #06b6d4);
    }}
    .toolbar {{
      margin-top: 16px;
    }}
    .hero-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 22px;
    }}
    button, select, input {{
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 12px;
      font: inherit;
    }}
    select, input {{
      padding: 0 12px;
      background: white;
    }}
    button {{
      padding: 0 16px;
      border-color: var(--primary);
      color: white;
      background: var(--primary);
      cursor: pointer;
      font-weight: 800;
    }}
    .secondary-button, .filter-button {{
      border-color: rgba(79, 70, 229, 0.20);
      color: var(--primary-dark);
      background: var(--primary-soft);
    }}
    .danger-button {{
      border-color: #fecaca;
      color: #991b1b;
      background: #fff1f2;
    }}
    .action-grid, .connection-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }}
    .connection-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .action-card {{
      display: block;
      padding: 18px;
    }}
    .action-card span {{
      display: grid;
      place-items: center;
      width: 32px;
      height: 32px;
      margin-bottom: 12px;
      border-radius: 50%;
      color: white;
      background: var(--primary);
      font-weight: 900;
    }}
    .action-card strong {{
      display: block;
      margin-bottom: 6px;
      letter-spacing: -0.03em;
    }}
    .action-card small {{
      color: var(--muted);
    }}
    .connection-card, .settings-card {{
      padding: 20px;
    }}
    .connection-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}
    .connection-head h3, .settings-card h3, .empty-state h3 {{
      margin: 4px 0 0;
      letter-spacing: -0.04em;
    }}
    .setup-panel {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      align-items: end;
      padding: 20px;
    }}
    .setup-panel label {{
      display: grid;
      gap: 7px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
    }}
    .setup-panel p {{
      grid-column: 1 / -1;
      margin: 0;
    }}
    .filter-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }}
    .brief-grid {{
      display: grid;
      gap: 14px;
    }}
    .brief-card {{
      padding: 22px;
    }}
    .brief-card ul {{
      display: grid;
      gap: 10px;
      margin: 16px 0 0;
      padding: 0;
      list-style: none;
    }}
    .claim {{
      display: flex;
      align-items: flex-start;
      gap: 10px;
      margin: 0;
      padding: 12px 14px;
      border-radius: 16px;
      background: var(--surface-strong);
    }}
    .claim::before {{
      content: "";
      flex: 0 0 auto;
      width: 8px;
      height: 8px;
      margin-top: 9px;
      border-radius: 50%;
      background: var(--success);
    }}
    .claim-text {{
      flex: 1;
      min-width: 0;
    }}
    .claim-sources {{
      display: block;
      margin-top: 7px;
      color: var(--muted);
      font-size: 12px;
    }}
    .claim-sources a {{
      color: var(--primary);
      font-weight: 800;
    }}
    .claim-status {{
      flex: 0 0 auto;
      color: var(--warning);
      background: #fff7ed;
    }}
    .source-list {{
      display: grid;
      gap: 12px;
    }}
    .source-card {{
      padding: 16px;
    }}
    .source-card h3 {{
      margin-top: 10px;
      font-size: 16px;
    }}
    .source-card p {{
      margin: 10px 0 0;
      color: #475569;
      font-size: 14px;
    }}
    .source-meta {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .source-link {{
      display: inline-block;
      max-width: 100%;
      overflow: hidden;
      color: var(--primary);
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .muted {{
      color: var(--muted);
    }}
    .error-text {{
      color: #b91c1c;
      font-size: 14px;
      font-weight: 700;
    }}
    .danger-zone {{
      margin-top: 14px;
      border-color: #fecaca;
      background: #fff7f7;
    }}
    .empty-state {{
      padding: 24px;
      border: 1px dashed var(--line);
      border-radius: var(--radius);
      color: var(--muted);
      background: rgba(255, 255, 255, 0.64);
    }}
    @media (max-width: 1100px) {{
      body {{ min-width: 0; }}
      main {{ width: min(100% - 28px, 960px); }}
      .hero, .project-hero, .dashboard-layout {{
        grid-template-columns: 1fr;
      }}
      .metric-grid, .project-grid {{
        grid-template-columns: 1fr;
      }}
      .action-grid, .connection-grid, .setup-panel {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    {body}
  </main>
</body>
</html>"""


def render_brief(
    brief: BriefRevision,
    state: ApprovalRead | None,
    members: list[ProjectMember],
    source_lookup: dict[str, SourceItem],
) -> str:
    approval_html = render_approval_panel(brief, state, members)
    sections = []
    for section in brief.content.get("sections", []):
        claims = "\n".join(
            "<li class='claim'>"
            "<span class='claim-text'>"
            f"{html.escape(claim.get('text', ''))}"
            f"{render_claim_sources(claim, source_lookup)}"
            "</span>"
            f"<code class='claim-status'>{html.escape(claim.get('status', ''))}</code>"
            "</li>"
            for claim in section.get("claims", [])
        )
        if not claims:
            claims = "<li class='claim'><span class='claim-text muted'>No claims.</span></li>"
        section_title = html.escape(section.get("title", section.get("key", "Section")))
        section_key = html.escape(section.get("key", "section"))
        article_open = (
            f"<article class='brief-card brief-section' data-section='{section_key}'>"
            f"<h3>{section_title}</h3>"
        )
        sections.append(
            f"{article_open}<ul>{claims}</ul></article>"
        )
    return (
        "<div class='brief-toolbar' id='brief'>"
        f"<p><strong>Revision v{brief.version}</strong></p>"
        f"<span class='status-badge'>{html.escape(brief.status.value.replace('_', ' '))}</span>"
        "</div>"
        f"{render_brief_filters()}"
        f"{approval_html}"
        f"<div class='brief-grid'>{''.join(sections)}</div>"
    )


def render_brief_filters() -> str:
    filters = [
        ("all", "전체"),
        ("decisions", "결정사항"),
        ("tasks", "할 일"),
        ("design_changes", "디자인 피드백"),
        ("schedule_risks", "일정 리스크"),
    ]
    buttons = "\n".join(
        "<button class='filter-button' type='button' "
        f"onclick=\"filterBrief('{key}')\">{label}</button>"
        for key, label in filters
    )
    return f"""
    <div class="filter-row">
      {buttons}
    </div>
    <script>
      function filterBrief(key) {{
        document.querySelectorAll('.brief-section').forEach((section) => {{
          section.style.display = key === 'all' || section.dataset.section === key ? '' : 'none';
        }});
      }}
    </script>
    """


def render_claim_sources(claim: dict, source_lookup: dict[str, SourceItem]) -> str:
    source_ids = claim.get("source_item_ids") or []
    if not source_ids:
        return "<small class='claim-sources'>근거 없음 · AI 추론 여부 확인 필요</small>"
    links = []
    for source_id in source_ids[:4]:
        item = source_lookup.get(str(source_id))
        label = item.provider.value if item else "source"
        links.append(
            f"<a href='#source-{html.escape(str(source_id))}'>"
            f"{html.escape(label)}:{html.escape(str(source_id)[:8])}</a>"
        )
    return f"<small class='claim-sources'>근거: {' · '.join(links)}</small>"


def render_approval_panel(
    brief: BriefRevision,
    state: ApprovalRead | None,
    members: list[ProjectMember],
) -> str:
    if state is None:
        return ""
    member_options = "\n".join(
        f"<option value='{member.id}'>{html.escape(member.display_name)} "
        f"({html.escape(member.email)})</option>"
        for member in members
    )
    approved_percent = (
        int((state.approved_count / state.required_count) * 100) if state.required_count else 0
    )
    pending_member_ids = html.escape(", ".join(state.pending_member_ids) or "none")
    return f"""
    <article class="approval-card">
      <div class="approval-header">
        <div>
          <p class="eyebrow">Local confirmation</p>
          <h3>검토 후 정리본 확정</h3>
        </div>
        <span class="status-badge">{state.approved_count} / {state.required_count}</span>
      </div>
      <div class="approval-progress" aria-label="approval progress">
        <span style="width: {approved_percent}%"></span>
      </div>
      <p class="muted">Pending member IDs: {pending_member_ids}</p>
      <div class="toolbar">
        <select id="member-id">{member_options}</select>
        <input id="api-key" placeholder="API key if configured">
        <button onclick="approveBrief()">Confirm revision</button>
      </div>
      <p id="approval-result" class="muted"></p>
    </article>
    <script>
      async function approveBrief() {{
        const memberId = document.getElementById('member-id').value;
        const apiKey = document.getElementById('api-key').value;
        const headers = {{'X-OpenBrief-Member-ID': memberId}};
        if (apiKey) headers['X-OpenBrief-API-Key'] = apiKey;
        const response = await fetch(
          '/api/v1/projects/{brief.project_id}/briefs/{brief.id}/approve',
          {{method: 'POST', headers}}
        );
        document.getElementById('approval-result').textContent = response.ok
          ? 'Confirmed. Refresh to see the updated state.'
          : 'Confirmation failed: ' + await response.text();
      }}
    </script>
    """


def render_source_item(item: SourceItem) -> str:
    source_url = html.escape(item.source_url or "")
    link = f"<a class='source-link' href='{source_url}'>{source_url}</a>" if source_url else ""
    occurred_at = item.occurred_at.strftime("%Y-%m-%d %H:%M")
    return (
        f"<article class='source-card' id='source-{item.id}'>"
        "<div class='source-meta'>"
        f"<code>{html.escape(item.provider.value)}</code>"
        f"<code>{html.escape(item.kind.value.replace('_', ' '))}</code>"
        "</div>"
        f"<h3>{html.escape(item.title)}</h3>"
        f"<p>{html.escape(item.body[:500])}</p>"
        f"<p class='muted'>{html.escape(occurred_at)} · {link}</p>"
        "</article>"
    )
