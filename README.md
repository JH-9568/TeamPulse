# OpenBrief

OpenBrief는 Figma, Notion, Discord, GitHub 등에 흩어진 프로젝트 맥락을 내 컴퓨터에 모아 정리하는 오픈소스 로컬 앱입니다.

디자인 시안, 회의 내용, 기획 문서, 할 일, 완료된 일, 일정 변경, PR/Issue/Commit 같은 정보를 공식 API로 읽고, AI가 근거 기반 프로젝트 브리프를 만듭니다.

현재 방향은 명확합니다.

- 원본 서비스에는 기본적으로 반영하지 않습니다.
- OpenBrief는 먼저 읽고, 정리하고, 근거를 남깁니다.
- AI가 만든 정리본은 검토용 초안입니다.
- 기본 모드는 개인 로컬 프로젝트입니다.
- 멤버/승인 기능은 나중에 팀 사용을 위한 고급 기능으로 남겨둡니다.
- 로컬 앱 MVP에서는 Webhook보다 API polling을 우선 사용합니다.

## 현재 구현 상태

구현된 기능:

- Python 3.12 / FastAPI 기반 API 서버
- macOS 로컬 앱처럼 쓰는 CLI
  - `openbrief init`
  - `openbrief setup`
  - `openbrief sync`
  - `openbrief sync --brief`
  - `openbrief brief`
  - `openbrief start`
  - `openbrief start --daemon`
  - `openbrief status`
  - `openbrief stop`
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
pipx install "git+https://github.com/JH-9568/OpenBrief.git"
```

## AI 에이전트로 설치하기

README를 직접 따라 하지 않아도 됩니다. Codex, Cursor, Claude Code 같은 개발 에이전트에게 아래 프롬프트를 그대로 붙여넣으면 설치, 초기 설정, 동작 확인을 맡길 수 있습니다.

중요한 보안 원칙:

- API key나 token은 명령어 인자로 넣지 않습니다.
- `--token`, `--openai-api-key`, `--github-token` 방식은 shell history에 남을 수 있으므로 피합니다.
- token 입력은 `openbrief auth ...` 숨김 입력을 사용합니다.
- OpenBrief는 기본적으로 원본 Figma, Notion, Discord, GitHub를 수정하지 않고 읽기/정리만 합니다.

### 설치 프롬프트

```text
내 컴퓨터에 OpenBrief를 설치하고 초기 실행까지 도와줘.

조건:
- 공식 저장소는 https://github.com/JH-9568/OpenBrief
- Python 3.12 이상이 있는지 확인해줘.
- 가능하면 pipx로 설치해줘.
- 설치 명령은 `pipx install "git+https://github.com/JH-9568/OpenBrief.git"`를 사용해줘.
- 설치 후 `openbrief --help`를 실행해서 정상 설치 여부를 확인해줘.
- 그 다음 `openbrief init`을 실행해줘.
- OpenBrief는 로컬 앱이고 기본 대시보드는 http://127.0.0.1:8000/dashboard 라는 점을 설명해줘.
- API key나 token이 필요한 단계에서는 내가 직접 입력하게 해줘.
- token을 명령어 인자로 넣지 말고 `openbrief auth ...` 방식의 숨김 입력을 사용해줘.
```

### GitHub 공개 저장소 테스트 프롬프트

```text
OpenBrief가 제대로 동작하는지 GitHub 공개 저장소로 테스트해줘.

조건:
- `openbrief setup --project "OpenBrief" --github-repo "JH-9568/OpenBrief"` 실행
- `openbrief sync --provider github --brief` 실행
- `openbrief start` 실행
- 브라우저에서 http://127.0.0.1:8000/dashboard 를 열어서 프로젝트와 브리프가 보이는지 확인
- 문제가 생기면 로그 위치와 해결 방법을 알려줘.
```

### 내 프로젝트 연결 프롬프트

```text
내 실제 프로젝트를 OpenBrief에 연결해줘.

연결하고 싶은 도구:
- GitHub
- Figma
- Notion
- Discord

주의:
- token/API key는 절대 shell history에 남기지 마.
- `--token`, `--openai-api-key`, `--github-token` 같은 인자 방식은 피하고 `openbrief auth ...`를 사용해.
- 각 서비스에서 필요한 값이 무엇인지 먼저 알려줘.
- 내가 값을 준비하면 하나씩 연결하고 `openbrief sync --brief`로 테스트해줘.
- 수집된 내용은 로컬 DB에 저장되고, 원본 Figma/Notion/Discord/GitHub는 수정하지 않는다는 점을 확인해줘.
```

### AI 요약 설정 프롬프트

```text
OpenBrief에서 AI 요약을 사용할 수 있게 설정해줘.

조건:
- OpenAI-compatible API key는 내가 직접 입력할게.
- `openbrief auth openai`를 사용해 숨김 입력으로 저장해줘.
- 저장 후 `openbrief brief` 또는 `openbrief sync --brief`를 실행해서 AI 요약이 동작하는지 확인해줘.
- key가 config.toml에 평문 저장되지 않는지 확인해줘.
```

## 5분 Quickstart

Public GitHub 저장소는 토큰 없이도 바로 테스트할 수 있습니다. 먼저 OpenBrief 자기 자신을 수집해 브리프를 만들어볼 수 있습니다.

```bash
openbrief init

openbrief setup \
  --project "OpenBrief" \
  --github-repo "JH-9568/OpenBrief"

openbrief sync --provider github --brief
openbrief start
```

브라우저에서 확인합니다.

```text
http://127.0.0.1:8000/dashboard
```

AI 품질을 높이려면 OpenAI API key를 저장한 뒤 다시 브리프를 생성합니다.

```bash
openbrief auth openai
openbrief brief
```

설치 후 초기화합니다.

```bash
openbrief init
```

로컬 웹앱을 실행합니다.

```bash
openbrief start
```

브라우저에서 접속합니다.

```text
http://127.0.0.1:8000/dashboard
```

백그라운드로 실행하려면:

```bash
openbrief start --daemon
openbrief status
openbrief stop
```

## macOS `.app` launcher 만들기

OpenBrief는 기본적으로 CLI로 설치하고 브라우저에서 쓰는 로컬 웹앱입니다. 더블클릭으로 실행하고 싶다면 무료 `.app` launcher를 만들 수 있습니다.

```bash
pipx install "git+https://github.com/JH-9568/OpenBrief.git"
git clone https://github.com/JH-9568/OpenBrief.git
cd OpenBrief
scripts/build_macos_app.sh
open dist/OpenBrief.app
```

이 launcher는 새 앱 번들이지만 OpenBrief 본체를 포함하지 않습니다. 내부적으로 설치된 `openbrief start --daemon`을 실행하고 `http://127.0.0.1:8000/dashboard`를 엽니다.

현재 `.app`은 서명/공증되지 않습니다.

- `.app` 생성 비용: 무료
- GitHub Release 배포: 무료
- Apple Developer Program 서명/공증: 선택 사항, 연 $99

서명하지 않은 앱은 macOS Gatekeeper 경고가 뜰 수 있습니다. 초기 오픈소스 배포에서는 정상적인 한계입니다.

## 개발 환경에서 실행하기

레포를 클론한 상태에서는 editable install로 실행할 수 있습니다.

```bash
python -m pip install -e ".[dev]"
openbrief init
openbrief start
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

`openbrief setup`으로 프로젝트와 외부 소스를 등록합니다.

```bash
openbrief setup \
  --project "Brand Renewal Sprint" \
  --figma-file-url "https://www.figma.com/file/..." \
  --figma-token "figd_..." \
  --notion-page-url "https://www.notion.so/..." \
  --notion-token "secret_..." \
  --discord-channel-id "1234567890" \
  --discord-bot-token "..." \
  --github-repo "JH-9568/OpenBrief" \
  --github-token "github_pat_..."
```

토큰을 명령어 인자로 넣으면 shell history에 남을 수 있습니다. 실사용에서는 `openbrief auth`로 숨김 입력하는 방식을 권장합니다.

```bash
openbrief auth openai
openbrief auth figma
openbrief auth notion
openbrief auth discord
openbrief auth github
```

등록 후 데이터를 수집합니다.

```bash
openbrief sync
```

수집 직후 AI 브리프까지 만들려면:

```bash
openbrief sync --brief
```

특정 provider만 수집할 수도 있습니다.

```bash
openbrief sync --provider figma
openbrief sync --provider notion
openbrief sync --provider discord
openbrief sync --provider github
```

수집된 데이터는 SourceItem으로 정규화되어 로컬 DB에 저장됩니다.

브리프만 따로 생성할 수도 있습니다.

```bash
openbrief brief
```

## 로컬 앱 데이터 위치

기본 경로:

```text
~/.openbrief/
  config.toml
  openbrief.db
  openbrief.pid
  run.json
  logs/
```

provider token은 `openbrief setup` 또는 `openbrief auth` 시 로컬 SQLite DB에 암호화되어 저장됩니다.

OpenAI API key와 provider token 암호화 키는 가능한 경우 OS credential store에 저장됩니다.

- macOS: Keychain
- Windows: Credential Manager
- Linux: Secret Service/keyring backend

사용 중인 환경에 credential store가 없으면 `~/.openbrief/.secrets.json`으로 fallback합니다. 이 fallback 파일은 권한 `600`으로 생성되지만 OS Keychain 수준의 보호는 아닙니다. 회사/팀 실사용에서는 OS credential store가 동작하는 환경을 권장합니다.

보안상 권장되는 입력 방식:

```bash
openbrief auth openai
openbrief auth figma
openbrief auth notion
openbrief auth discord
openbrief auth github
```

`--token`, `--openai-api-key`, `--github-token` 같은 인자 입력은 shell history에 남을 수 있으므로 테스트 외에는 권장하지 않습니다.

## 왜 Webhook이 아니라 Polling인가?

로컬 앱은 사용자의 맥북에서 실행됩니다.

Figma, Notion, GitHub, Discord 같은 외부 서비스는 사용자의 `localhost`로 Webhook을 직접 보낼 수 없습니다.

그래서 로컬 앱 MVP에서는 다음 방식이 현실적입니다.

```text
OpenBrief가 각 서비스 공식 API를 주기적으로 조회
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

OpenBrief가 실제 사용자 데이터를 가져오는 방식은 각 서비스의 공식 API입니다.

- Figma REST API
- Notion API
- Discord Bot API
- GitHub REST API
- Slack Web API / Events API 예정

MCP는 개발 과정에서 Codex가 브라우저나 외부 도구를 제어할 때 사용할 수 있는 개발 보조 수단입니다. OpenBrief 사용자가 제품을 쓰기 위해 MCP를 설치해야 하는 구조로 만들지 않습니다.

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

Figma, Notion, Discord, GitHub에서 가져온 내용은 그대로 보면 정리되어 있지 않습니다. 그래서 OpenBrief는 수집된 SourceItem을 다시 읽고 프로젝트 브리프를 생성합니다.

OpenAI API key를 넣으면 기본적으로 OpenAI 호환 `chat/completions` endpoint를 호출합니다.

```bash
openbrief auth openai
openbrief sync --brief
```

OpenAI 호환 서버를 직접 지정할 수도 있습니다.

```bash
openbrief setup \
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
4. 웹 설정 화면에서 안전한 token 입력 UX 추가
5. Discord `/status`, `/blockers`, `/meeting-end` 명령 처리
6. Slack 연동
7. credential store 상태 점검/진단 명령 추가
8. Cloud/Webhook 버전 설계
