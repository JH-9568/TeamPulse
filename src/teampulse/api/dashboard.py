import html
import uuid
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teampulse.briefs.service import approval_state
from teampulse.db import get_session
from teampulse.models import BriefRevision, Project, ProjectMember, SourceItem
from teampulse.schemas import ApprovalRead

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
    return HTMLResponse(render_project_dashboard(project, brief, state, members, source_items))


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
        project_rows = "<div class='empty-state'>No projects yet.</div>"
    body = f"""
    <section class="hero">
      <div>
        <p class="eyebrow">TeamPulse Local</p>
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
    """
    return html_page("TeamPulse", body)


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


def render_project_dashboard(
    project: Project,
    brief: BriefRevision | None,
    state: ApprovalRead | None,
    members: list[ProjectMember],
    source_items: list[SourceItem],
) -> str:
    brief_html = render_brief(brief, state, members) if brief else "<p>No brief revisions yet.</p>"
    sources_html = "\n".join(render_source_item(item) for item in source_items)
    if not sources_html:
        sources_html = "<div class='empty-state'>No source evidence yet.</div>"
    provider_counts = Counter(item.provider.value for item in source_items)
    provider_pills = "\n".join(
        f"<span class='provider-pill provider-{html.escape(provider)}'>"
        f"{html.escape(provider)} <strong>{count}</strong></span>"
        for provider, count in sorted(provider_counts.items())
    )
    if not provider_pills:
        provider_pills = "<span class='provider-pill'>No sources</span>"
    approval_text = "No brief"
    if state is not None:
        approval_text = f"{state.approved_count}/{state.required_count} approvals"
    brief_status = html.escape(brief.status.value.replace("_", " ")) if brief else "not generated"
    body = f"""
    <nav class="topbar">
      <a class="brand" href="/dashboard">
        <span class="brand-mark">TP</span>
        <span>TeamPulse</span>
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
          <p class="eyebrow">Source Evidence</p>
          <h2>수집된 원본 근거</h2>
        </div>
        <div class="source-list">{sources_html}</div>
      </aside>
    </section>
    """
    return html_page(f"TeamPulse - {project.name}", body)


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
    .metric-card, .approval-card, .brief-card, .source-card {{
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
) -> str:
    approval_html = render_approval_panel(brief, state, members)
    sections = []
    for section in brief.content.get("sections", []):
        claims = "\n".join(
            "<li class='claim'>"
            f"<span class='claim-text'>{html.escape(claim.get('text', ''))}</span>"
            f"<code class='claim-status'>{html.escape(claim.get('status', ''))}</code>"
            "</li>"
            for claim in section.get("claims", [])
        )
        if not claims:
            claims = "<li class='claim'><span class='claim-text muted'>No claims.</span></li>"
        section_title = html.escape(section.get("title", section.get("key", "Section")))
        sections.append(
            f"<article class='brief-card'><h3>{section_title}</h3>"
            f"<ul>{claims}</ul></article>"
        )
    return (
        "<div class='brief-toolbar'>"
        f"<p><strong>Revision v{brief.version}</strong></p>"
        f"<span class='status-badge'>{html.escape(brief.status.value.replace('_', ' '))}</span>"
        "</div>"
        f"{approval_html}"
        f"<div class='brief-grid'>{''.join(sections)}</div>"
    )


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
        const headers = {{'X-TeamPulse-Member-ID': memberId}};
        if (apiKey) headers['X-TeamPulse-API-Key'] = apiKey;
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
        "<article class='source-card'>"
        "<div class='source-meta'>"
        f"<code>{html.escape(item.provider.value)}</code>"
        f"<code>{html.escape(item.kind.value.replace('_', ' '))}</code>"
        "</div>"
        f"<h3>{html.escape(item.title)}</h3>"
        f"<p>{html.escape(item.body[:500])}</p>"
        f"<p class='muted'>{html.escape(occurred_at)} · {link}</p>"
        "</article>"
    )
