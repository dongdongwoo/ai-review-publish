# 데이터 처리

이 패키지는 러너에 **명시적으로 전달된 diff 파일**과 호출자가 선택적으로 넘긴 메타데이터만 처리합니다.

## 입력

- 로컬 diff 또는 패치 파일
- 선택 MR 메타데이터 (repository, MR URL, ticket, branch, commit SHA, changed area, business flow)
- 이 패키지의 리뷰 정책 Markdown
- 프롬프트 메타로 쓰는 모델명 (선택)
- 프롬프트 메타로 쓰는 severity threshold (선택)
- GitLab 게시 단계에서 사용하는 MR URL

## 출력

- `outputs/` 또는 호출자가 지정한 경로의 Markdown 리뷰
- 제한된 `review-context.md`
- `inline-anchor-guide.md`, `inline-anchor-guide.json`
- `context-coverage.json`
- 검증된 `inline-candidates.json`
- 인라인 후보 경고 파일
- GitLab publication plan JSON
- 슬라이스 리뷰 시 `outputs/parts/<slice>/` 아래 슬라이스 diff/리뷰/인라인 후보
- 슬라이스 합본 `outputs/review.md`, `outputs/inline-candidates.json`

## 저장 동작

패키지는 설정된 출력 디렉터리 아래에 요청된 리뷰 출력과 생성된 컨텍스트 산출물만 기록합니다.

자격 증명은 패키지 파일에 쓰지 않습니다. 승인 AI CLI와 GitLab 토큰은 **호출자 환경**(`.env` 등)에서 제공합니다.

## 외부 AI CLI

승인된 AI CLI 선택은 호출자(팀) 책임입니다. 러너는 프롬프트를 standard input으로 넘기고 standard output을 캡처합니다.

이 패키지에는 API 제공자가 하드코딩되어 있지 않습니다.
