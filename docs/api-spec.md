# TeamPulse API Spec

Base URL: `/api/v1`

The MVP API is designed for a future dashboard and for provider ingestion. Authentication is not production-ready yet; approval endpoints temporarily use `X-TeamPulse-Member-ID`.

## Workspaces and Projects

### Create workspace

`POST /workspaces`

```json
{
  "name": "Design Team",
  "timezone": "Asia/Seoul"
}
```

### Create project

`POST /projects`

```json
{
  "workspace_id": "uuid",
  "name": "Mobile App Redesign",
  "description": "Q3 product redesign",
  "daily_report_channel_id": "discord-channel-id"
}
```

### Get project

`GET /projects/{project_id}`

## Members

### Create member

`POST /projects/{project_id}/members`

```json
{
  "display_name": "Jin",
  "email": "jin@example.com",
  "role": "designer"
}
```

### List members

`GET /projects/{project_id}/members`

Active members are snapshotted when a brief revision is created.

## Integrations

### Create integration

`POST /projects/{project_id}/integrations`

```json
{
  "provider": "figma",
  "external_id": "figma-file-or-webhook-id",
  "name": "Main design file",
  "credentials": {
    "access_token": "secret"
  },
  "config": {
    "file_key": "abc123"
  }
}
```

If `credentials` is present, `TOKEN_ENCRYPTION_KEY` must be configured.

Discord integration example:

```json
{
  "provider": "discord",
  "external_id": "discord-channel-id",
  "name": "Project Discord channel",
  "credentials": {
    "bot_token": "discord-bot-token"
  },
  "config": {
    "channel_id": "discord-channel-id",
    "poll_limit": 50
  }
}
```

Figma integration example:

```json
{
  "provider": "figma",
  "external_id": "figma-file-key",
  "name": "Main design",
  "credentials": {
    "access_token": "figma-token"
  },
  "config": {
    "file_key": "figma-file-key"
  }
}
```

### List integrations

`GET /projects/{project_id}/integrations`

### Poll Discord integration

`POST /projects/{project_id}/integrations/{integration_id}/poll`

Implemented providers:

- Discord: fetches channel messages after `config.last_message_id`, stores them as source items, then updates `config.last_message_id`.
- Figma: fetches file metadata and comments, stores them as source items, then updates `config.last_synced_at`.

```json
{
  "integration_id": "uuid",
  "channel_id": "discord-channel-id",
  "fetched": 12,
  "stored": 10,
  "duplicates": 2,
  "last_message_id": "discord-snowflake"
}
```

## Source Items

### List project source items

`GET /projects/{project_id}/source-items`

Query parameters:

- `since`: optional ISO datetime lower bound.
- `until`: optional ISO datetime upper bound.
- `provider`: optional provider filter, for example `discord`, `figma`, `notion`.
- `kind`: optional source kind filter, for example `meeting_message`, `design_comment`.

This endpoint is the evidence feed for a project brief.

### Manual/dev ingest

`POST /source-items/ingest`

```json
{
  "project_id": "uuid",
  "provider": "discord",
  "external_id": "discord:message-id",
  "kind": "meeting_message",
  "title": "Discord meeting note",
  "body": "Decision: use variant B.",
  "source_url": "https://discord.com/channels/...",
  "occurred_at": "2026-07-18T10:00:00+09:00",
  "actor": { "username": "jin" },
  "metadata": { "channel_id": "123" },
  "raw_payload": {}
}
```

Duplicate detection uses `(provider, external_id)`.

## Webhooks

### Figma

`POST /webhooks/figma/{project_id}?integration_id={integration_id}`

Expected provider behavior:

- Figma sends JSON webhook events such as `PING`, `FILE_UPDATE`, and `FILE_COMMENT`.
- The payload includes `passcode`.
- TeamPulse compares it to `FIGMA_WEBHOOK_PASSCODE`.
- `PING` verifies connectivity only and does not create a source item.

Stored source kinds:

- `FILE_COMMENT` -> `design_comment`
- other file events -> `design_update`

### Notion

`POST /webhooks/notion/{project_id}?integration_id={integration_id}`

Expected provider behavior:

- Initial verification requests contain `verification_token`; TeamPulse accepts them without creating source items.
- Event requests include `X-Notion-Signature`.
- TeamPulse validates HMAC-SHA256 using `NOTION_WEBHOOK_VERIFICATION_TOKEN`.

Stored source kinds:

- page/block events -> `planning_doc`
- database/data source events -> `task_change`

## Briefs

### Generate daily brief

`POST /projects/{project_id}/briefs/generate`

```json
{
  "since": "2026-07-18T00:00:00+09:00",
  "until": "2026-07-18T23:59:59+09:00"
}
```

Creates a new pending revision, snapshots active members, and supersedes previous pending revisions.

### List briefs

`GET /projects/{project_id}/briefs`

### Get brief

`GET /projects/{project_id}/briefs/{revision_id}`

Returns the full brief revision content, source IDs, approver snapshot, status,
and revision hash.

### Edit brief

`POST /projects/{project_id}/briefs/{revision_id}/edit`

```json
{
  "created_by": "jin@example.com",
  "content": {
    "sections": [
      {
        "key": "decisions",
        "title": "Decisions",
        "claims": [
          {
            "text": "Use variant B for onboarding.",
            "status": "confirmed",
            "source_item_ids": ["uuid"]
          }
        ]
      }
    ],
    "source_window": {},
    "diff_from_last_confirmed": []
  }
}
```

Editing creates a new revision and invalidates previous approvals by changing revision identity/hash.

### Approve brief

`POST /projects/{project_id}/briefs/{revision_id}/approve`

Header:

`X-TeamPulse-Member-ID: {project_member_id}`

The revision becomes `confirmed` only when all snapshotted active members have approved the same revision hash.

### Get approval state

`GET /projects/{project_id}/briefs/{revision_id}/approval-state`

```json
{
  "revision_id": "uuid",
  "revision_hash": "sha256",
  "required_count": 3,
  "approved_count": 2,
  "pending_member_ids": ["uuid"],
  "status": "pending_approval"
}
```

### Send Discord brief reminder

`POST /projects/{project_id}/briefs/{revision_id}/notify-discord`

Sends one Discord message for the revision. The endpoint is idempotent: if the
same revision was already delivered to the Discord channel, it returns
`duplicate: true` and does not send another message.

```json
{
  "brief_revision_id": "uuid",
  "channel_id": "discord-channel-id",
  "delivered": true,
  "duplicate": false,
  "external_message_id": "discord-message-id"
}
```
