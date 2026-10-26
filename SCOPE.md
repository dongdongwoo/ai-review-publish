# 범위

이 패키지는 diff → Markdown AI 리뷰 흐름을 위한 독립 실행형 리뷰 봇입니다.

## 포함 범위

- diff 파일 읽기
- 제한된 MR 리뷰 컨텍스트 생성
- 변경 diff 줄에서 인라인 앵커 가이드 생성
- 일반 엔지니어링 또는 blockchain/DeFi 리뷰 정책 적용
- 공통 리뷰 프롬프트 생성
- 승인된 외부 AI CLI 호출
- 구조화된 인라인 후보 파싱 및 검증
- raw 모델 출력을 human-readable 리뷰로 후처리
- 출력에서 secret 및 로컬/내부 경로 유출 차단
- Markdown 리뷰 저장
- GitLab publication plan 준비
- apply 모드에서 리뷰 요약 및 검증된 인라인 후보 게시
- 핵심 리뷰 플로우 fixture 테스트
- `rich` / `concise` human-readable 출력 모드
- 리뷰 파이프라인 및 실행 설정 문서화
- 큰 MR을 도메인/모듈 슬라이스로 나눠 리뷰한 뒤 요약/인라인을 합쳐 한 번 게시

## 동작 방식

호출자가 제공하는 입력으로 실행합니다: diff/패치, 선택 MR 메타, 리뷰 정책, 승인 AI CLI, 게시 시 GitLab MR URL.

자격 증명 및 엔터프라이즈 연동은 이 패키지 파일이 아니라 **호출자 환경**에서 관리합니다.
