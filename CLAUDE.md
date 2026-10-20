# realDefinition — 작업 규칙 (Claude Code가 이 저장소에서 일할 때 따른다)

## 가장 먼저: Intent 주도 개발
**코드를 쓰기 전에 [Intent.md](Intent.md)의 "현재 작업"을 먼저 쓰고, 개발은 기획 문서를 ID로 참조해서 한다.** 작업이 끝나면 Intent를 "작업 기록"으로 내리고 기획 문서를 코드와 맞춘다. 자세한 흐름과 체크리스트는 Intent.md 0절에 있다.

1. **Intent 5항목** — 문제(Problem), 기대 결과(Proposed outcome), 영향 범위(Affected users and systems), 제약(Constraints), 열린 질문(Open questions). 기대 결과는 측정 가능하게 쓴다.
2. **참조 추적표** — 아래 문서에서 해당 ID를 찾아 적는다. 문서에 없는 ID는 코드보다 **문서를 먼저** 추가한다.
3. **열린 질문 중 개발을 막는 것은 사용자와 정한 뒤** 개발한다. 추측으로 채우지 않는다(가정이면 `[가정]`).
4. **개발 후** PRD·IA·UJ·ERD·구현 현황 문서를 코드와 맞추고, 새 결정은 `D-번호`(되돌리기 어려우면 ADR)로 기록한다.

## 참조 문서 (순서대로 읽는다)
| 종류 | 위치 | ID |
|---|---|---|
| Intent(의도) | [Intent.md](Intent.md) | `INT-번호` |
| PRD(요구사항) | [docs/03-prd.md](docs/03-prd.md) | `F1`~, `NF1`~ |
| UJ(사용자 여정) | [docs/04-journey-and-flow.md](docs/04-journey-and-flow.md) 1~2절 | `CUJ-1`~ |
| IA(정보 구조)·화면 | 같은 문서 3~4절 | `IA-A`~, `S1`~ |
| ERD | [erd.md](erd.md) | `스키마.테이블` |
| 결정·ADR | [docs/02-thinking-log.md](docs/02-thinking-log.md), [docs/adr/](docs/adr/) | `D-번호`, `ADR-번호` |
| 구현 현황 | [docs/12](docs/12-poc-desk-app.md), [14](docs/14-supabase-spec-and-tickets.md), [16](docs/16-vercel-deployment.md) | — |
| 구현 설계(라이브러리·버전·파일 역할·단계·테스트·한도) | [docs/17](docs/17-technical-design.md) | `R1`~`R7`, `G1`~`G8` |

이미 정한 결정(D-번호)과 동결된 실험 파일(`experiment/` 의 실행 기록·골드·질문·사전)은 **뒤집거나 수정하지 않는다.** 뒤집으려면 새 결정으로 기록한다(D-017, D-026, D-032).

## 지켜야 할 것
- **비밀:** `.env`, `.claude`는 커밋하지 않는다. API 키·비밀번호를 채팅이나 문서에 쓰지 않는다(사용자가 `.env`에 직접 넣는다).
- **커밋·푸시:** 사용자가 요청할 때만 한다. 변경은 성격별(feat/fix/test/docs/chore/perf)로 나눠 커밋하고, 커밋 메시지는 한국어로 쓴다.
- **개인 데이터:** 실제 가계부(`data/private/`)는 저장소·공개 앱·클라우드에 올리지 않는다.
- **문서:** 한국어, front-matter(`id`, `title`, `status`, `updated`, `depends_on`), 초보도 읽을 수 있게 📘 개념 학습 상자와 `[가정]` 표시를 쓴다. 문서 규격은 README 하단 참고.
- **정직성:** 확인하지 못한 것은 "확인하지 못했다"고 쓴다. 표본이 작으면 정확도를 주장하지 않는다. 실패한 시험도 기록한다.
- **테스트:** 코드를 바꾸면 `python app/test_app.py`, `python experiment/test_experiment.py`를 돌린다(환경변수 `POC_DESK_BACKEND`는 비워 두거나 `sqlite`).

## 새 라이브러리 정책
- 표준 라이브러리를 먼저 쓴다. 외부 라이브러리는 순수 파이썬이고 정확한 버전으로 고정(`requirements.txt`)할 수 있을 때만, **사용자 승인 후** 넣는다. 자세한 기준은 [docs/17](docs/17-technical-design.md) 4절.
- 작업 전 Intent의 구현 계획(6~9항목)을 채우고, 끝나면 회귀 관문 G1~G8을 통과시킨다.
