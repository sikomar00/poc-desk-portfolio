---
id: README
title: PoC Desk — 문제 정의 프레임워크 포트폴리오
status: draft
updated: 2026-10-19
---

# PoC Desk — 문제 정의 프레임워크 포트폴리오

> **처음 보시는 분은 [PORTFOLIO.md](PORTFOLIO.md)(1~2분 요약)부터 읽으세요.** 아래는 전체 문서 목차입니다.

> "모호한 요구를, 검증 가능한 과제로 바꾸고, 만들어 보고, 숫자와 기록으로 증명한다."
결과물만 보여 주는 것이 아니라 **왜 이 문제를 골랐고, 어떻게 생각했고, 무엇을 배웠는지**를 문서로 남깁니다.

## 앱 실행 (화면)

```powershell
python app/server.py
```

추가 설치 없이 Python만 있으면 `http://127.0.0.1:8765`에서 열립니다. 질문 → 정의 확인 → 계산 → 정답 대조 → 리포트 흐름을 눌러 볼 수 있습니다. 자세한 내용은 [docs/12-poc-desk-app.md](docs/12-poc-desk-app.md).

## 한 줄 요약

| 항목 | 내용 |
|---|---|
| 핵심 문제 | 지표 정의(예: "매출")가 문서화되지 않아 같은 질문에 담당자·AI마다 다른 숫자가 나옴 |
| 해결 방향 | 요구 한 줄 → 모호함 질문 → KPI 정의서 확정 → SQL 생성·실행 → 합계 검증 → PoC 리포트 |
| 두 공고 연결 | AI-BI: KPI 정의·Text-to-SQL·RAG / R&D: PoC 설계·검증·문서화 |

## 문서 읽는 순서

| 순서 | 문서 | 답하는 질문 | 상태 |
|---|---|---|---|
| 0 | [용어집](docs/00-glossary.md) | 이 문서에 나오는 말이 무슨 뜻인가? | draft |
| 1 | [문제 정의서](docs/01-problem-definition.md) | 무엇이 문제이고, 누구의 문제이며, 어떻게 알 수 있나? | draft |
| 2 | [사고 흐름 기록](docs/02-thinking-log.md) | 왜 그렇게 결정했나? 무엇을 버렸나? | draft |
| 3 | [PRD](docs/03-prd.md) | 무엇을 어디까지 만들고, 언제 성공이라 하나? | draft |
| 4 | [사용자 여정 · 크리티컬 여정 · 화면 흐름](docs/04-journey-and-flow.md) | 사용자는 어떤 길로 가고, 어디가 끊기면 안 되나? | draft |
| 5 | [데이터 설계](docs/05-data-design.md) | 어떤 데이터로 문제를 재현하고, 정답은 어떻게 정하나? | draft |
| 6 | [실험 설계](docs/06-experiment-design.md) | KPI 사전이 AI의 숫자를 정말 안정시키는지 어떻게 재나? | draft |
| 7 | [실험 결과](docs/07-experiment-results.md) | 실제로 재 보니 어땠나? (두 모델 완료, 종합 판정 불명) | draft |
| 8 | [후속 실험 설계(동결)](docs/08-experiment-v2-design.md) | 결과를 보고 고친 사전은 정말 나아졌나? 처음 보는 질문으로 어떻게 확인하나? | draft |
| 9 | [후속 실험 결과](docs/09-experiment-v2-results.md) | v2는 나아졌나? 부작용은? 검증 방법은 새 질문에서도 통하나? | draft |
| 10 | [회고](docs/10-retrospective.md) | 무엇을 배웠고, 무엇을 틀렸고, 다음에 무엇을 하나? | draft |
| 11 | [면접 준비 노트](docs/11-interview-prep.md) | 예상 질문에 어떤 근거로 답하나? 내 말로 설명할 수 있나? | draft |
| 12 | [PoC Desk 앱](docs/12-poc-desk-app.md) | 설계한 흐름을 눌러 볼 수 있는 화면은? 무엇을 확인했고 무엇은 못 했나? | draft |

코드·데이터: `app/` (PoC Desk 로컬 웹앱), `data/` (합성 샘플 DB 생성기, 정의별 검증 스크립트, 가계부 CSV 점검·정의별 계산·앱 합계 대조 스크립트), `experiment/` (실험용 KPI 사전, 질문 20개와 골드 답, H3 테스트 세트). 실데이터는 `data/private/`(Git 제외).


## 문서 규격 (모든 문서 공통)

1. **front-matter**: `id`, `title`, `status`(draft/review/final), `updated`, `depends_on`.
2. **고정 구조**: `요약 → 본문 → 📘 개념 학습 → 열린 질문 → 변경 이력`.
3. **📘 개념 학습 박스**: 그 문서에서 처음 나온 개념을 초보자 눈높이로 설명.
4. **결정은 근거와 함께**: "무엇을 / 왜 / 버린 대안"을 [사고 흐름 기록](docs/02-thinking-log.md)에 번호(D-001…)로 누적.
5. **가정은 가정이라고 표시**: `[가정]` 태그를 붙이고 검증 방법을 적음. 아직 확인하지 않은 것을 사실처럼 쓰지 않음.
6. **다이어그램은 Mermaid**: 텍스트라서 버전 관리와 비교가 쉬움.
