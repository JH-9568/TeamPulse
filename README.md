# TeamPulse

TeamPulse는 Figma, Notion, Discord, GitHub 등에 흩어진 프로젝트 맥락을 내 컴퓨터에 모아 정리하는 오픈소스 로컬 앱입니다.

디자인 시안, 회의 내용, 기획 문서, 할 일, 완료된 일, 일정 변경, PR/Issue/Commit 같은 정보를 공식 API로 읽고, AI가 근거 기반 프로젝트 브리프를 만듭니다.

현재 방향은 명확합니다.

- 원본 서비스에는 기본적으로 반영하지 않습니다.
- TeamPulse는 먼저 읽고, 정리하고, 근거를 남깁니다.
- AI가 만든 정리본은 검토용 초안입니다.
- 기본 모드는 개인 로컬 프로젝트입니다.
- 멤버/승인 기능은 나중에 팀 사용을 위한 고급 기능으로 남겨둡니다.
- 로컬 앱 MVP에서는 Webhook보다 API polling을 우선 사용합니다.

## 현재 구현 상태

구현된 기능:

- Python 3.12 / FastAPI 기반 API 서버
- macOS 로컬 앱처럼 쓰는 CLI
  - `teampulse init`
  - `teampulse setup`
  - `teampulse sync`
  - `teampulse sync --brief`
  - `teampulse brief`
  - `teampulse start`
  - `teampulse start --daemon`
  - `teampulse status`
  - `teampulse stop`
- 로컬 SQLite DB 기본 실행
- PostgreSQL / SQLAlchemy 2.x 모델
- Alembic 마이그레이션
- Figma REST API sync
  - 파일 메타데이터
  - 댓글
- Notion REST API sync
  - 페이지 메타데이터
  - 블록 텍스트
- Discord Bot API polling
  - 지정 채널 메시지 수집
  - 명령어 메시지 구분
- GitHub REST API sync
  - Issues
  - Pull Requests
  - Commits
  - GitHub Actions workflow runs
- SourceItem 정규화 및 중복 저장 방지
- 일일 브리프 생성
  - deterministic fallback summarizer
  - OpenAI API key 기반 AI summarizer 옵션
- 브리프 검토/확정 상태
  - 기본은 로컬 owner 검토
  - 멤버 기반 전원 승인 구조는 내부 모델로 유지
- Discord 일일 브리프 알림 전송
- Celery worker / scheduler 구조
- Docker Compose 개발 환경
- pytest 테스트

아직 미완성인 부분:

- 클릭 기반 웹 설정 화면
- Slack 연동
- 원본 서비스에 write-back 하는 양방향 동기화
- 사용자/조직/로그인/권한 관리
- production-grade 배포/모니터링
- macOS `.app` 또는 `.dmg` 패키징

## 설치해서 쓰기

일반 사용자 설치 방식은 `pipx`를 권장합니다.

```bash
pipx install "git+https://github.com/JH-9568/TeamPulse.git"
```

설치 후 초기화합니다.

```bash
teampulse init
```

로컬 웹앱을 실행합니다.

```bash
teampulse start
```

브라우저에서 접속합니다.

```text
http://127.0.0.1:8000/dashboard
```

백그라운드로 실행하려면:

```bash
teampulse start --daemon
teampulse status
teampulse stop
```

## 개발 환경에서 실행하기

레포를 클론한 상태에서는 editable install로 실행할 수 있습니다.

```bash
python -m pip install -e ".[dev]"
teampulse init
teampulse start
```

또는 Docker Compose로 실행합니다.

```bash
cp .env.example .env
docker compose up --build
```

다른 터미널에서:

```bash
docker compose exec api alembic upgrade head
curl http://localhost:8000/health
```

API 문서:

```text
http://localhost:8000/docs
```

프로젝트 대시보드:

```text
http://localhost:8000/dashboard
```

## 실제 프로젝트 연결하기

`teampulse setup`으로 프로젝트와 외부 소스를 등록합니다.

```bash
teampulse setup \
  --project "Brand Renewal Sprint" \
  --figma-file-url "https://www.figma.com/file/..." \
  --figma-token "figd_..." \
  --notion-page-url "https://www.notion.so/..." \
  --notion-token "secret_..." \
  --discord-channel-id "1234567890" \
  --discord-bot-token "..." \
  --github-repo "JH-9568/TeamPulse" \
  --github-token "github_pat_..." \
  --openai-api-key "sk-..."
```

등록 후 데이터를 수집합니다.

```bash
teampulse sync
```

수집 직후 AI 브리프까지 만들려면:

```bash
teampulse sync --brief
```

특정 provider만 수집할 수도 있습니다.

```bash
teampulse sync --provider figma
teampulse sync --provider notion
teampulse sync --provider discord
teampulse sync --provider github
```

수집된 데이터는 SourceItem으로 정규화되어 로컬 DB에 저장됩니다.

브리프만 따로 생성할 수도 있습니다.

```bash
teampulse brief
```

## 로컬 앱 데이터 위치

기본 경로:

```text
~/.teampulse/
  config.toml
  teampulse.db
  teampulse.pid
  run.json
  logs/
```

provider token은 `teampulse setup` 시 로컬 SQLite DB에 암호화되어 저장됩니다. OpenAI API key는 `~/.teampulse/config.toml`에 저장됩니다.

암호화 키는 현재 `~/.teampulse/config.toml`에 저장됩니다. 따라서 `~/.teampulse` 디렉터리는 일반 앱 데이터처럼 보호해야 합니다.

향후 macOS Keychain 연동으로 개선할 예정입니다.

## 왜 Webhook이 아니라 Polling인가?

로컬 앱은 사용자의 맥북에서 실행됩니다.

Figma, Notion, GitHub, Discord 같은 외부 서비스는 사용자의 `localhost`로 Webhook을 직접 보낼 수 없습니다.

그래서 로컬 앱 MVP에서는 다음 방식이 현실적입니다.

```text
TeamPulse가 각 서비스 공식 API를 주기적으로 조회
↓
변경분을 로컬 DB에 저장
↓
AI가 정리
↓
대시보드에서 확인
```

Cloud 버전에서는 Webhook/Event API를 사용하는 구조로 확장할 수 있습니다.

## MCP를 쓰나요?

제품 기능으로는 MCP를 사용하지 않습니다.

TeamPulse가 실제 사용자 데이터를 가져오는 방식은 각 서비스의 공식 API입니다.

- Figma REST API
- Notion API
- Discord Bot API
- GitHub REST API
- Slack Web API / Events API 예정

MCP는 개발 과정에서 Codex가 브라우저나 외부 도구를 제어할 때 사용할 수 있는 개발 보조 수단입니다. TeamPulse 사용자가 제품을 쓰기 위해 MCP를 설치해야 하는 구조로 만들지 않습니다.

## 외부 서비스 권한

### Figma

필요한 값:

- Figma personal access token
- Figma file URL 또는 file key

수집 대상:

- 파일 메타데이터
- 파일 댓글

### Notion

필요한 값:

- Notion integration token
- Notion page URL 또는 page id

수집 대상:

- 페이지 메타데이터
- 페이지 블록 텍스트

주의:

- Notion integration이 해당 페이지에 초대되어 있어야 합니다.

### Discord

필요한 값:

- Discord bot token
- Discord channel id

필요 권한:

- `VIEW_CHANNEL`
- `READ_MESSAGE_HISTORY`
- `SEND_MESSAGES`

주의:

- 메시지 내용 접근은 Discord의 Message Content 정책 영향을 받을 수 있습니다.

### GitHub

필요한 값:

- `owner/repo` 형식의 repository
- GitHub token

Public repo는 token 없이도 일부 조회가 가능하지만 rate limit이 낮습니다.

수집 대상:

- Issues
- Pull Requests
- Commits
- GitHub Actions workflow runs

## AI 요약

Figma, Notion, Discord, GitHub에서 가져온 내용은 그대로 보면 정리되어 있지 않습니다. 그래서 TeamPulse는 수집된 SourceItem을 다시 읽고 프로젝트 브리프를 생성합니다.

OpenAI API key를 넣으면 기본적으로 OpenAI 호환 `chat/completions` endpoint를 호출합니다.

```bash
teampulse setup \
  --project "Brand Renewal Sprint" \
  --github-repo "JH-9568/TeamPulse" \
  --openai-api-key "sk-..."

teampulse sync --brief
```

OpenAI 호환 서버를 직접 지정할 수도 있습니다.

```bash
teampulse setup \
  --project "Brand Renewal Sprint" \
  --openai-api-key "..." \
  --ai-url "https://api.openai.com/v1/chat/completions" \
  --ai-model "gpt-4.1-mini"
```

설정하지 않거나 호출에 실패하면 deterministic fallback summarizer를 사용합니다.

중요 정책:

```text
AI는 원본 도구를 수정하지 않습니다.
AI는 정리 초안을 만듭니다.
사용자가 확인하고 확정해야 합니다.
```

## 테스트

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests scripts
pytest
```

현재 테스트는 다음 범위를 포함합니다.

- API key auth
- SourceItem 중복 저장 방지
- Figma sync
- Notion sync
- Discord polling
- GitHub sync
- 브리프 생성/검토
- 대시보드 렌더링
- CLI init/setup/sync/brief/status

## 문서

- [Architecture](docs/architecture.md)
- [API Spec](docs/api-spec.md)
- [ERD](docs/erd.md)

## 추천 개발 순서

다음으로 할 일:

1. 웹 대시보드에 `Sync now` / `Generate brief` 버튼 추가
2. 웹 대시보드에 개인 프로젝트/연동 설정 화면 추가
3. 실제 Figma/Notion/Discord/GitHub token으로 end-to-end sync 검증
4. OpenAI API key 입력 UX 개선
5. Discord `/status`, `/blockers`, `/meeting-end` 명령 처리
6. Slack 연동
7. macOS Keychain 토큰 저장
8. Cloud/Webhook 버전 설계
