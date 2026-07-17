# TeamPulse

TeamPulse collects project context from Figma, Notion, and Discord, then produces a cited daily project brief that the whole team can review and approve.

The product direction is intentionally read-only for source systems:

- TeamPulse reads explicitly connected Figma files, Notion pages/databases, and Discord channels.
- TeamPulse creates internal draft brief revisions and sends one Discord reminder.
- TeamPulse never edits Figma, Notion, Discord, GitHub, or Slack in the MVP.
- A brief becomes confirmed only when every snapshotted active project member approves the same revision hash.

## MVP Scope

Implemented in this repository:

- FastAPI application structure.
- PostgreSQL/SQLAlchemy 2.x models.
- Alembic initial migration.
- Figma and Notion webhook ingestion boundaries.
- Figma REST sync for file metadata and comments.
- Notion REST sync for page metadata and block text.
- Discord integration polling that stores opted-in channel messages as source items.
- Source item normalization and idempotent storage.
- Daily brief revision generation with a deterministic summarizer fallback.
- Optional OpenAI-compatible AI summarizer endpoint.
- Discord daily brief reminder delivery with duplicate protection.
- Celery Beat daily scheduler for polling, brief generation, and reminder delivery.
- Revision approval state with unanimous approval.
- Docker Compose for API, worker, PostgreSQL, and Redis.
- pytest coverage for idempotent ingestion and unanimous approval.

Not implemented yet:

- Production authentication and organization management.
- Real LLM provider integration.
- Production-grade schedule locking for multi-instance deployments.
- Web dashboard UI.
- Slack/GitHub integrations and source write-back.

## Local Setup

```bash
cp .env.example .env
docker compose up --build
```

In another shell:

```bash
docker compose exec api alembic upgrade head
curl http://localhost:8000/health
```

API docs are available at `http://localhost:8000/docs`.

If `API_KEY` is set, protected API routes require:

```bash
X-TeamPulse-API-Key: your-api-key
```

If `AI_SUMMARIZER_URL` is set, TeamPulse calls that OpenAI-compatible
chat/completions endpoint for brief generation. If it is empty or the call
fails, TeamPulse uses the deterministic fallback summarizer.

To create a local demo workspace/project/source-items/brief:

```bash
docker compose exec api python scripts/demo_seed.py
```

## Tests

```bash
python -m pip install -e ".[dev]"
pytest
ruff check .
```

## Key External Requirements

- Figma: needs `file_content:read`, `file_comments:read`, and `webhooks:write` if TeamPulse creates webhooks. Figma webhook events include a `passcode` that must match the configured secret.
- Notion: needs read content and read comments capabilities for the connected pages/databases. Notion webhook requests should be verified with `X-Notion-Signature` using the subscription verification token.
- Discord: needs a bot installed in opted-in channels with `VIEW_CHANNEL`, `READ_MESSAGE_HISTORY`, and `SEND_MESSAGES` for reminders. Message content availability depends on Discord's privileged Message Content policy.

## Documents

- [Architecture](docs/architecture.md)
- [API Spec](docs/api-spec.md)
- [ERD](docs/erd.md)
