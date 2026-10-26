# 리뷰 파이프라인

이 문서는 공통 리뷰 패키지의 실행 흐름과 기대 출력 형식을 설명합니다.

## 입력

- MR diff 또는 패치 파일
- 선택 MR 메타데이터:
  - repository (저장소)
  - MR URL
  - ticket (티켓)
  - source / target branch
  - commit SHA
  - changed area (변경 영역)
  - business flow (비즈니스 흐름)
- 리뷰 정책 Markdown
- 호출자가 설정한 승인 AI CLI
- GitLab 게시(apply) 시 GitLab 토큰

## 런타임 설정

| 변수 | 설명 | 기본값 |
|---|---|---|
| `AI_REVIEW_COMMAND` | stdin으로 프롬프트를 읽고 stdout으로 리뷰 Markdown 출력 | (없음) |
| `AI_REVIEW_MR_URL` | diff fetch, 게시, 프롬프트 메타에 쓰는 MR URL | (없음) |
| `AI_REVIEW_DIFF` | 리뷰 입력 패치 파일 경로 | `outputs/mr.diff` |
| `AI_REVIEW_OUTPUT` | human-facing 리뷰 Markdown 경로 | `outputs/review.md` |
| `AI_REVIEW_INLINE_JSON` | 검증된 인라인 후보 JSON 경로 | `outputs/inline-candidates.json` |
| `AI_REVIEW_MODEL` | 프롬프트에 포함되는 모델 메타 | `enterprise-approved-model` |
| `AI_REVIEW_SEVERITY_THRESHOLD` | human output/인라인에 남길 최소 심각도 | `medium` |
| `AI_REVIEW_OUTPUT_FORMAT` | `rich` 또는 `concise` | `rich` |
| `AI_REVIEW_POLICY` | 리뷰 정책 파일 | `policies/generic-engineering.md` |
| `AI_REVIEW_MR_CONTEXT` | MR별 합의/범위 노트. 정책 뒤에 붙음 | (없음, 예: `policies/mr-context.md`) |
| `AI_REVIEW_MAX_DIFF_LINES` | diff 전체 줄 상한 | `2000` |
| `AI_REVIEW_PER_FILE_DIFF_LINES` | 파일당 diff 줄 상한 | `300` |
| `AI_REVIEW_DRY_RUN` | `1`이면 AI 호출 없이 프롬프트만 저장 | `0` |
| `AI_REVIEW_SLICES` | 큰 MR용 슬라이스 설정 JSON | (없음, 예: `slices/example.json`) |
| `AI_REVIEW_PARTS_DIR` | 슬라이스별 산출물 디렉터리 | `outputs/parts` |
| `AI_REVIEW_ONLY_SLICES` | 실행할 슬라이스 이름 (쉼표 구분) | (없음) |
| `AI_REVIEW_RERUN` | `1`이면 기존 슬라이스 `review.md`가 있어도 다시 리뷰 | `0` |
| `AI_REVIEW_INCLUDE_PATHS` | 단일 리뷰에서 포함할 경로 prefix/glob (쉼표 구분) | (없음) |
| `AI_REVIEW_EXCLUDE_PATHS` | 단일 리뷰에서 제외할 경로 prefix/glob (쉼표 구분) | (없음) |
| `AI_REVIEW_GITLAB_APPLY` | `1`이면 GitLab에 게시 | `0` |
| `AI_REVIEW_GITLAB_PUBLISH_INLINE` | `1`이면 인라인 diff 코멘트도 게시 | `0` |

MR 패치 받기:

```bash
bash scripts/fetch-mr-diff.sh
```

## 파이프라인 단계

1. 호출자가 제공한 diff/패치 읽기
2. 제한된 리뷰 컨텍스트 생성:
   - MR 메타데이터
   - 선택된 리뷰 정책
   - 변경 파일 요약
   - 제한된 diff 발췌
   - coverage 메타데이터
3. 변경 diff 줄에서 인라인 앵커 가이드 생성
4. AI 프롬프트 생성:
   - 리뷰 컨텍스트
   - 인라인 앵커 가이드
   - 모델 메타
   - severity threshold
   - 출력 형식
5. 승인된 AI CLI에 프롬프트 전달
6. AI 원본 출력 저장
7. `Inline comment candidates` JSON 추출
8. 앵커 가이드 기준으로 인라인 후보 검증
9. human-facing Markdown 리뷰 후처리
10. secret/로컬 경로 출력 가드 실행
11. 아래 파일 저장:
    - `outputs/review.md`
    - `outputs/review.raw.md`
    - `outputs/inline-candidates.json`
    - `outputs/inline-candidate-warnings.txt`
    - `outputs/context/review-context.md`
    - `outputs/context/inline-anchor-guide.md`
    - `outputs/context/inline-anchor-guide.json`
    - `outputs/context/context-coverage.json`

큰 MR은 같은 단계를 슬라이스마다 반복한 뒤 합칩니다.

```bash
bash scripts/fetch-mr-diff.sh
bash scripts/suggest-slices.sh slices/my-mr.json
AI_REVIEW_SLICES=slices/my-mr.json bash scripts/run-sliced-review.sh
```

`run-sliced-review.sh`는 diff를 도메인/모듈로 나누고, 슬라이스마다 리뷰를 돌린 다음 `outputs/review.md`와 `outputs/inline-candidates.json`으로 합칩니다. GitLab 게시는 그 합본으로 **한 번만** 하면 됩니다.`

## 출력 형식

기본값은 `rich`입니다. GitLab 상세 리뷰 스타일과 맞춥니다:

1. Review verdict
2. Findings table
3. Executive summary
4. 심각도 순 Findings
5. Missing tests
6. Execution issues / coverage gaps
7. Possible false positives or uncertainty
8. 검증된 인라인 후보 (JSON)

더 짧은 MR 코멘트가 필요할 때만 `AI_REVIEW_OUTPUT_FORMAT=concise`를 사용합니다.

## GitLab 게시

게시는 리뷰 생성과 **별도 단계**입니다.

같은 마커의 기존 요약 코멘트는 업데이트합니다. 인라인은 Resolve/답글이 있는 쓰레드를 유지하고, 같은 줄의 열린 AI 코멘트만 갱신합니다.

게시 계획만 (dry-run):

```bash
bash scripts/publish-gitlab.sh
```

apply — 요약 (+ 선택 시 인라인):

```bash
# .env: AI_REVIEW_GITLAB_APPLY=1
# 인라인: AI_REVIEW_GITLAB_PUBLISH_INLINE=1
set -a && . ./.env && set +a
bash scripts/publish-gitlab.sh
```

PowerShell:

```powershell
$env:GITLAB_TOKEN = "token-from-approved-environment"
$env:AI_REVIEW_MR_URL = "https://gitlab.example.com/group/project/-/merge_requests/123"
.\scripts\publish-gitlab.ps1 `
  -Review outputs\review.md `
  -InlineJson outputs\inline-candidates.json `
  -Apply `
  -PublishInline
```

## MR 검증 권장 순서

1. MR diff를 로컬 패치로 저장 (`fetch-mr-diff.sh`)
2. `AI_REVIEW_OUTPUT_FORMAT=rich`로 리뷰 실행
3. `outputs/review.md` 확인
4. `outputs/inline-candidates.json` 확인
5. GitLab 게시 dry-run
6. 사람이 승인한 뒤에만 apply

MR 내용, 모델, AI CLI, 프롬프트 컨텍스트, severity threshold가 다르면 run마다 finding이 달라질 수 있습니다. 파이프라인과 출력 형식은 패키지가 담당하고, AI CLI/모델 선택은 호출자(팀)가 담당합니다.
