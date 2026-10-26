# AI Review Agent

GitLab MR diff → AI 리뷰 → (선택) GitLab 코멘트 게시.

| 방식 | 설정 | 실행 |
| --- | --- | --- |
| **로컬** | `AI_REVIEW_MR_URL` — 리뷰할 MR 하나 | [로컬 실행](#로컬-실행) |
| **GitHub Actions** | `AI_REVIEW_GITLAB_PROJECT` — GitLab `group/repo` (또는 project id) | [promotion 자동 리뷰](#github-actions-promotion-mr) |

상세: [REVIEW_PIPELINE.md](REVIEW_PIPELINE.md)

## 요구 사항

- Python 3.10+
- `GITLAB_TOKEN` (GitLab `api` scope)
- AI CLI — stdin 프롬프트 → stdout 리뷰 (예: Claude Code)

macOS/Linux 기준. Windows는 `scripts/*.ps1`.

## 설정

```bash
cp .env.example .env
```

필수:

```bash
GITLAB_TOKEN=...
AI_REVIEW_MR_URL=https://gitlab.example.com/group/project/-/merge_requests/123
AI_REVIEW_COMMAND="claude -p --output-format text --max-turns 3"
```

- MR URL만 바꿔도 프로젝트/MR 번호는 자동 파싱.
- 정책: `AI_REVIEW_POLICY=policies/generic-engineering.md` 또는 `policies/blockchain-defi.md`
- 처음에는 `AI_REVIEW_GITLAB_APPLY=0` 권장.
- 나머지: [.env.example](.env.example)

Claude Code는 한 번 `/login` 필요.

`.env` 수정 후 터미널에서 다시 로드:

```bash
set -a && . ./.env && set +a
```

## 로컬 실행

```bash
bash scripts/fetch-mr-diff.sh
bash scripts/run-review.sh          # 작은 MR
# bash scripts/run-sliced-review.sh # 큰 MR (slices/*.json)
head -10 outputs/review.md          # "## 1. Review verdict" 이면 OK
```

GitLab **미리보기** (게시 안 함):

```bash
AI_REVIEW_GITLAB_APPLY=0 bash scripts/publish-gitlab.sh
```

GitLab **게시**:

```bash
# .env: AI_REVIEW_GITLAB_APPLY=1, AI_REVIEW_GITLAB_PUBLISH_INLINE=1
bash scripts/publish-gitlab.sh
```

큰 MR·슬라이스 규칙: [REVIEW_PIPELINE.md](REVIEW_PIPELINE.md), `slices/example.json`

## GitHub Actions (promotion MR)

워크플로: **Watch promotion MRs** — 6시간마다, `promotion` 라벨 **열린 MR** 검색 → 아직 안 돌렸거나 **head SHA 변경** 시 리뷰 → **`outputs/mr-<iid>/`** 저장 (GitLab 자동 게시 없음).

### GitHub 설정 (한 번만)

1. Repo **Settings → Environments** → 예: **`PROD_ENV`** (워크플로 `environment:` 이름과 동일해야 함).
2. 그 **Environment** 안에 등록 (Repository Actions 탭이 **아님**):

| 종류 | 이름 | 값 |
| --- | --- | --- |
| Secret | `GITLAB_TOKEN` | GitLab PAT (`api`) |
| Secret | `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code |
| Variable | `AI_REVIEW_GITLAB_PROJECT` | `group/repo` (예: `hwis-tft-group/.../flash-lending`) |

선택 Variable: `GITLAB_BASE_URL`, `AI_REVIEW_WATCH_LABEL` (기본 `promotion`)

3. 이 변경을 **push**한 뒤 Actions → **Watch promotion MRs** → **Run workflow**로 확인.

결과: `outputs/mr-<iid>/review.md`, `outputs/watch-state.json`, Artifact / Job summary.

기존 promotion MR 일괄 리뷰 방지 (선택):

```bash
python3 src/watch_promotion.py --init
```

GitLab 게시: **Publish MR review** (`mr_iid`) 또는 `bash scripts/publish-mr.sh <iid>`

## 문제 해결

| 증상 | 확인 |
| --- | --- |
| 예전 MR로 fetch/publish | `.env` 저장 후 `set -a && . ./.env && set +a` |
| `AI command failed` | Claude `/login`, `AI_REVIEW_COMMAND` |
| `review.md`가 프롬프트만 | `AI_REVIEW_DRY_RUN=1` 실행했음 → `run-review.sh` 재실행 |
| 큰 MR 앞부분만 리뷰 | `run-sliced-review.sh` + slices JSON |

테스트: `bash tests/run-tests.sh`
