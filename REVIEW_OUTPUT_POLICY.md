# 리뷰 출력 정책

기본 출력은 MR을 검토하는 사람이 읽기 쉽도록 하면서, run 간 상세 리뷰 형식을 일관되게 유지하는 것을 목표로 합니다.

## 출력 모드

- **`rich` (기본):** review verdict, findings table, executive summary, 심각도 순 finding, missing tests, execution gaps, uncertainty, 인라인 후보 포함
- **`concise`:** 같은 리뷰 의도를 유지하되 MR 코멘트를 더 짧게

## 기본으로 게시되는 내용

- merge recommendation이 포함된 review verdict
- findings table
- executive summary
- 기본적으로 Critical, High, Medium finding
- missing tests 및 중요한 미커버 케이스
- execution issues / coverage gaps
- possible false positives or uncertainty
- 파일/줄을 특정할 수 있는 인라인 가능 finding
- 줄에 앵커할 수 없을 때만 non-inline finding

## 기본으로 숨기는 내용

Low 및 Info finding은 명시적으로 요청하지 않는 한 human review 본문에서 제외합니다.

상세 추론, raw 분석, 광범위한 탐색 노트는 기본적으로 MR 리뷰 코멘트에 붙이지 않습니다.

## Finding 규칙

각 finding은 다음을 포함해야 합니다:

- severity (심각도)
- 안정적인 finding ID (`C1`, `H1`, `M1` 등)
- file (파일)
- line 또는 anchor (가능한 경우)
- issue (문제)
- risk (영향)
- suggested fix (수정 방향)

추측성 주장은 피합니다. 컨텍스트가 부족하면 finding으로 단정하지 말고 coverage gap으로 표시합니다.

## 인라인 후보 규칙

인라인 후보는 `schemas/inline-candidates.schema.json`에 정의된 JSON 형식을 따라야 합니다.

파서가 남기는 후보 조건:

- 설정된 severity threshold 이상
- 유효한 finding ID
- 생성된 inline anchor guide의 file, line, line_type과 **정확히 일치**
- 비어 있지 않은 review text
- confidence `0`~`1`

## GitLab 게시

GitLab 게시 시 human-readable 리뷰가 MR **요약 코멘트**로 사용됩니다.

인라인 discussion은 `inline-candidates.json`에서 **검증된 후보만** 사용합니다.

요약 코멘트는 기본적으로 **짧게** 게시합니다 (`AI_REVIEW_GITLAB_SUMMARY=auto`):

- verdict + findings table 유지
- executive summary는 2~3문장으로 축약
- 인라인 후보가 있으면 Findings에 **finding별 짧은 요약**만 나열 (Issue/Risk/Suggested fix 전문은 inline에)
- Usage note는 GitLab 요약에서 제외

로컬 `outputs/review.md`는 그대로 `rich` 전체 리뷰입니다. GitLab 요약만 별도로 압축합니다.

apply 시 같은 마커의 기존 요약 코멘트는 **덮어씁니다.** 인라인은 Resolve되었거나 답글이 있으면 유지하고, 같은 줄의 열린 AI 코멘트만 갱신하며, 새 줄만 추가합니다.

apply 전에 publication plan JSON을 먼저 작성합니다.
