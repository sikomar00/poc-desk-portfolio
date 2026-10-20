---
id: 17-technical-design
title: 구현 설계서 — 라이브러리·버전, 폴더·파일, 표, 단계, 테스트, 제약
status: draft
updated: 2026-10-20
depends_on: [03-prd, 04-journey-and-flow, 12-poc-desk-app, 14-supabase-spec-and-tickets, 16-vercel-deployment]
---

# 구현 설계서

## 요약
"무엇으로(라이브러리·버전), 어디에(폴더·파일·표), 어떤 순서로(단계), 어떻게 확인하고(테스트), 무엇을 조심하나(제약)"를 한 문서에 모은 **개발 기준서**입니다. [Intent.md](../Intent.md)의 "구현 계획"은 이 문서의 양식을 따르고, 새 작업을 시작할 때 이 문서를 먼저 읽습니다.

- **외부 라이브러리는 하나뿐입니다**(Postgres 접속용 `pg8000`과 그 간접 의존 4개, 모두 순수 파이썬). 새 라이브러리는 6절의 기준을 통과해야만 들입니다.
- 버전은 **설치된 값을 직접 읽어서** 적었고, 외부 서비스의 한도는 **공식 문서를 직접 열어** 확인했습니다(확인일 2026-10-20). 확인하지 못한 것은 `[미확인]`으로 표시했습니다.

> 📘 **개념 학습 — 왜 버전을 "정확히" 적을까**
> "pg8000을 쓴다"만 적으면 다음 달에 누가 설치할 때 다른 버전이 깔려 동작이 달라질 수 있습니다. 직접 쓰는 것뿐 아니라 그 라이브러리가 끌고 오는 **간접 의존**까지 같은 버전으로 고정해야 "내 컴퓨터에서 되던 것이 배포에서 안 되는" 일이 줄어듭니다.

## 1. 기술 스택과 정확한 버전

### 1-1. 앱이 실행될 때 쓰는 것
| 구성요소 | 버전 | 하는 일 | 확인 방법 · 비고 |
|---|---|---|---|
| Python (내 PC) | **3.11.9** | 로컬 실행·테스트 | `python --version` |
| Python (Vercel) | **3.12** (기본값) | 배포 실행. 지원: 3.12(기본)·3.13·3.14 | 배포 로그 `python3.12`, [공식](https://vercel.com/docs/functions/runtimes/python). **로컬(3.11)과 다르다 → 7절 위험 R5** |
| Python 표준 라이브러리 | 내장 | 서버(`http.server`), SQLite(`sqlite3`), 암호화 연결(`ssl`), `json`, `urllib`, `threading`, `datetime` | 설치 불필요 |
| **pg8000** | **1.31.2** (고정) | Supabase(Postgres) 접속 드라이버(순수 파이썬) | 최신은 1.31.5(2025-09-14). 요구 Python ≥3.9, BSD-3, [PyPI](https://pypi.org/project/pg8000/) |
| scramp | 1.4.17 | pg8000의 비밀번호 인증(SCRAM) | 요구 Python ≥3.10, 의존 `asn1crypto>=1.5.1`, [PyPI](https://pypi.org/project/scramp/) |
| asn1crypto | 1.5.1 | scramp의 의존 | `app/_vendor` 설치본 |
| python-dateutil | 2.9.0.post0 | pg8000의 의존(날짜 처리) | 〃 |
| six | 1.17.0 | python-dateutil의 의존 | 〃 |
| 화면 | 라이브러리 없음 | 순수 HTML·CSS·JS(`app/static/`) | 외부 CDN·프레임워크 없음(보안 정책 CSP `script-src 'self'`) |
| SQLite | Python 내장 | 로컬 모드의 합성 데이터(`data/poc_desk_sample.db`) | 실험 재현용으로 계속 사용 |

### 1-2. 서비스·도구
| 구성요소 | 버전·값 | 하는 일 | 비고 |
|---|---|---|---|
| Supabase Postgres | **17.11.0.003** (서울 `ap-northeast-2`) | 클라우드 DB | `get_project`로 확인 |
| Supabase 접속 | 풀러 `aws-0-ap-northeast-2.pooler.supabase.com` | 읽기 전용 역할 **5432(세션 모드)**, 서비스 역할 **6543(트랜잭션 모드)** | 사용자명 `역할.프로젝트ref` ([공식](https://supabase.com/docs/guides/database/connecting-to-postgres)) |
| Supabase Auth | 호스팅 서비스 | 이메일·비밀번호 로그인. 서버는 `/auth/v1/user`로 토큰 확인 | 가입 닫힘, 이메일 확인 꺼짐 |
| Supabase 서버 인증서 | `prod-ca-2021.crt` (유효 ~2031-04-26) | 연결의 서버 신원 확인 | `app/certs/` |
| NVIDIA build API | `https://integrate.api.nvidia.com/v1` (OpenAI 호환) | 라이브 AI 모드 | 모델 `openai/gpt-oss-20b`(기본) |
| Vercel | Hobby(무료) 플랜, 함수 지역 `icn1`(서울) | 배포·자동 배포 | 3-2절 한도 |
| Supabase CLI | **2.121.0** | 마이그레이션 적용, 설정 반영 | `npx supabase --version` |
| Vercel CLI | **64.7.0** | 수동 배포, 환경변수, 연결 | `npx vercel --version` |
| Node.js / npm | 24.20.0 / 11.19.0 | **CLI 실행용으로만** 필요(앱 실행에는 불필요) | |
| Git 호스팅 | GitHub `sikomar00/realDefinition` | 저장소, Vercel 연동 | |

### 1-3. 의존 관리 파일
`requirements.txt`는 위 5개를 **정확한 버전으로 전부 고정**합니다(`==`). `app/_vendor/`(git 제외)에 설치하고, Vercel은 이 파일로 설치합니다. 전체 목록을 고정하는 이유는 간접 의존이 배포 때마다 최신으로 바뀌는 것을 막기 위해서입니다.

## 2. 폴더·파일 구조와 역할

```
realDefinition/
├─ Intent.md                  작업 의도(작업마다 갱신) ── CLAUDE.md 작업 규칙
├─ api/index.py               Vercel 진입점(서버를 서버리스 함수로 실행)
├─ vercel.json                Vercel 설정: 함수 시간·포함 파일·경로 재작성
├─ .vercelignore              Vercel에 올리지 않을 것(비밀·개인 데이터)
├─ requirements.txt           외부 라이브러리 고정 목록(5개)
├─ app/                       ── 웹앱 ──
│  ├─ server.py   (292줄)     HTTP 서버: 라우팅, 보안 규칙(호스트·출처·속도·로그인 확인)
│  ├─ engine.py   (487줄)     두뇌: 질문 해석, KPI 사전, 실행, 대사, 리포트, 기록
│  ├─ compare.py  (49줄)      정의 비교: 매출 정의 6가지 계산
│  ├─ db.py       (323줄)     Supabase 접속 계층: 역할별 연결, 쿼리, 로그인 확인, 오류 문구
│  ├─ static/                 화면(순수 HTML·CSS·JS)
│  │  ├─ index.html, app.js(393줄), style.css(113줄)
│  ├─ certs/prod-ca-2021.crt  Supabase 공식 루트 인증서(공개 파일)
│  ├─ test_app.py (763줄)     앱 테스트 59개
│  └─ _vendor/                설치된 pg8000 등(git 제외)
├─ experiment/                ── 실험(동결: 수정 금지) ──
│  ├─ run_experiment.py       실행기·안전 검사·읽기 전용 실행 / llm_client.py LLM 호출
│  ├─ gold.py                 기준 계산식(SQLite)과 골드 답 / holdout.py 보류 질문
│  ├─ kpi_dictionary_A(.v2).json  KPI 사전 / questions_A*.json 질문
│  ├─ verification.py, h3_reconcile.py, verify_without_gold.py  검증 방법
│  ├─ runs/*.jsonl            실제 모델 호출 기록(5개, 동결)
│  └─ test_experiment.py      실험 코드 테스트 32개
├─ data/                      ── 데이터 ──
│  ├─ generate_sample_db.py   합성 주문 DB 생성(seed 고정) / poc_desk_sample.db
│  ├─ verify_definitions.py   매출 정의 6가지(V0~V5) — 정의 비교의 출처
│  ├─ export_demo_sql.py, export_app_seed_sql.py   Supabase 적재용 SQL 생성
│  ├─ profile_bank_export.py, compute_spending_variants.py, compare_with_app.py  가계부 분석(로컬 전용)
│  └─ private/                실제 가계부(git 제외)
├─ supabase/
│  ├─ config.toml             인증·API 설정(프로젝트에 반영)
│  └─ migrations/0001~0007    DB 구조·권한·RLS·색인(순서대로 적용된 이력)
├─ qa/                        QA 점검 화면(index.html) + make_config.py(공개 키로 설정 파일 생성)
├─ docs/  00~17 문서 + adr/   기획·결정·결과 문서
└─ erd.md  GLOSSARY.md  README.md  PORTFOLIO.md
```

### 2-1. 핵심 파일의 함수별 역할
| 파일 | 주요 함수·클래스 | 하는 일 |
|---|---|---|
| `app/server.py` | `Handler.do_GET/do_POST` | 요청 받기 → 경로 복원(`_restore_path`) → 호스트·출처·속도 확인 → 라우팅 |
| 〃 | `_user()` | `Authorization` 토큰이 유효한 본인 계정인지 `db.verify_token`으로 확인 |
| 〃 | 경로 | `GET /api/status·dictionary·history·run` / `POST /api/intake·definition·run·compare·report` |
| `app/engine.py` | `run_query` | 지표 확인 → 실행(계산식 또는 AI) → 대사 → 기록 저장 |
| 〃 | `reconcile` | 원장 대사(허용 오차 0), 기준 계산식 대조, "검증 불가" 판정 |
| 〃 | `compare_definitions` | 정의 비교 결과 만들기 |
| 〃 | `save_definition`·`dictionary` | 사용자 정의 지표(파일 저장)와 내장 지표 |
| `app/db.py` | `_Session` | 역할별 연결 하나를 재사용, 끊기면 한 번 재연결 |
| 〃 | `execute_readonly`·`query_rows` | 읽기 전용 역할로 쿼리 실행(모델 SQL / 고정 SQL) |
| 〃 | `ledger_total`·`record_run`·`history` | 서비스 역할로 원장 읽기·실행 기록 저장·조회 |
| 〃 | `verify_token` | Supabase Auth에 토큰을 보내 사용자 확인 + `app.profiles` 등록 확인 |
| 〃 | `friendly_error` | DB 오류를 한국어 문구로(내부 정보 숨김) |
| `app/compare.py` | `compare`·`to_postgres` | 정의 6가지 실행, SQLite→Postgres 날짜 함수 변환 |

## 3. 표 모양 (ERD 요약)
열 단위의 전체 정의는 [erd.md](../erd.md)의 "테이블 정의표"가 기준입니다. 여기서는 어느 코드가 어느 표를 읽고 쓰는지만 봅니다.

| 스키마.표 | 행 수(2026-10-20) | 핵심 열 | 읽는 쪽 | 쓰는 쪽 |
|---|---|---|---|---|
| `demo.customers` / `products` | 15 / 8 | `customer_id` / `product_id` | `poc_reader_demo`(모델·계산식), 공개(anon) | 적재 스크립트(관리자) |
| `demo.orders` | 500 | `order_id`, `paid_at`, `status`, `items_ex_vat` | 〃 | 〃 |
| `demo.order_items` / `refunds` | 1,017 / 52 | `order_id` → `orders` | 〃 | 〃 |
| `app.kpi_definitions` | 5 | `(dataset, metric_key, version)` 유일 | `app_service` | 시드 스크립트(관리자) |
| `app.ledger` | 3 | `(definition_id, period_month)` | `app_service` | 시드 스크립트 |
| `app.profiles` | 1 | `user_id` → `auth.users` | `app_service` | 관리자 |
| `app.query_runs` | 0 | `run_id`, `user_id`(비로그인은 NULL) | `app_service`(본인 것만) | `app_service`(추가만) |
| `personal.*` 4개 | 0 | `transactions.owner_id` → `app.profiles` | `poc_reader_personal` | 관리자(**앱 미연결**) |

## 4. 단계별 접근

### 4-1. 이 시스템이 만들어진 순서 (참고, 이미 끝남)
| 단계 | 한 것 | 새 라이브러리 |
|---|---|---|
| 0 | 문제 정의, 데이터 설계, 실험 2회 | 없음 |
| 1 | 로컬 웹앱(표준 라이브러리만) | 없음 |
| 2 | Supabase 이전(스키마·역할·RLS) | **pg8000**(유일) |
| 3 | 앱 연결, 로그인, 정의 비교 | 없음 |
| 4 | Vercel 공개 배포, 자동 배포 | 없음 |
| 5 | 라이브 AI(본인 전용) | 없음 |

### 4-2. 앞으로 기능을 만드는 표준 5단계 (각 단계 끝에 테스트가 통과해야 다음으로)
| 단계 | 하는 일 | 끝났다는 기준(게이트) |
|---|---|---|
| ① 의도·문서 | Intent 5항목과 구현 계획 작성, IA·UJ·PRD·ERD의 ID 확인·추가 | 열린 질문 중 개발을 막는 것이 모두 정해짐 |
| ② 데이터·권한 | 필요하면 마이그레이션 파일 추가(`supabase/migrations/`), 역할·RLS 점검 | 권한 시험 통과, 기존 표·열을 지우지 않음 |
| ③ 순수 로직 | `app/`의 모듈에 **함수로** 구현(입출력이 분명한 것부터), **시험을 먼저 또는 같이** | 함수 시험 통과, 기존 59+32개 통과 |
| ④ API·화면 | `server.py` 경로, `static/` 화면 연결. 빈·오류·로딩 상태까지 | 브라우저 시나리오 통과, 비로그인·로그인 둘 다 |
| ⑤ 배포·기록 | 푸시 → 자동 배포 확인, 공개 주소에서 직접 확인, 문서·QA 시트 갱신 | 배포 상태 success, 회귀 게이트 전부 통과 |

**새 라이브러리 정책:** ① 표준 라이브러리로 안 되는가 ② 직접 짜면 100줄 이하인가 ③ 유지보수되고 라이선스가 맞는가를 순서대로 따져서, **①②에서 해결되면 만들지 않는다.** 들인다면 순수 파이썬·고정 버전·사용자 승인 후에만(pg8000은 표준 라이브러리에 Postgres 드라이버가 없어서 유일한 예외). 검토했다가 들이지 않은 것: `psycopg`(컴파일된 바이너리, 배포 크기·호환 위험), `Flask`·`FastAPI`(서버가 표준 라이브러리로 충분), `supabase-py`(대부분 필요 없는 기능, 의존 많음).

## 5. 테스트 전략

| 층 | 무엇으로 | 무엇을 확인 | 언제 | 현재 규모 |
|---|---|---|---|---|
| **함수 시험** | `python app/test_app.py`, `python experiment/test_experiment.py` (표준 `unittest`, 네트워크·비밀번호 불필요: 가짜 연결·가짜 LLM·임시 폴더) | 해석·대사·정의 비교·SQL 변환·공개 모드 규칙·오류 문구·로그인 규칙 | 코드를 바꿀 때마다 | 앱 59 + 실험 32 |
| **연결 시험** | 실제 Supabase에 역할로 로그인해 쿼리(스크립트) | 권한(읽기 전용·5초·스키마 접근), RLS, 두 DB 값 일치, 연결 재사용·끊김 복구 | DB·권한·쿼리를 바꿀 때 | 기준 계산식 25개 조합, 정의 비교 18개 값 |
| **화면 눌러 보기** | 내장 브라우저에서 시나리오 실행(로컬 `8765`, 공개 주소) | 질문→결과→리포트→기록, 로그인·로그아웃, 비로그인 차단, 좁은 화면 | 화면·API를 바꿀 때 | 시나리오 약 30개 |
| **QA 시트** | 구글 시트 2개 시트(Claude QA 결과 / 사용자 QA 목록) | 위 결과의 기록과 사용자가 직접 확인할 항목 | 기능이 끝날 때 | Claude 73건 + 사용자 51건 |
| **배포 확인** | 푸시 후 GitHub 커밋 상태, 공개 주소 호출 | 자동 배포 성공, 공개 규칙(403/401/429), 속도 | 푸시마다 | 매번 |

### 5-1. 회귀 게이트 (지금 돌아가는 것을 깨지 않았다는 증거 — 작업마다 전부 통과)
| # | 확인 | 합격 기준 |
|---|---|---|
| G1 | `python app/test_app.py` (환경변수 `POC_DESK_BACKEND` 비움) | 59개 OK |
| G2 | `python experiment/test_experiment.py` | 32개 OK |
| G3 | 값 불변: 1분기 매출 / 정의 비교 6값 / 2월 5지표 | 62,831,000원(원장 대사 통과) / 82,537,900·74,296,400·67,234,000·64,101,000·62,960,300·62,831,000 / 18,287,000원·129건·141,760원·8.67%·7.75% |
| G4 | SQLite와 Supabase의 값이 같다 | 기준 계산식 25조합 불일치 0 |
| G5 | 동결 파일 불변 | `git diff`에 `experiment/runs/`, 질문·사전·골드 변경 없음(D-017, D-026, D-032) |
| G6 | 비밀 노출 없음 | 저장소 검색에서 `nvapi-` 0건, `.env`·`.claude` 미추적 |
| G7 | 공개 규칙 | 다른 출처 POST 403, 비로그인 정의 저장 403·기록 401·AI 401, 연속 요청 429 |
| G8 | 배포 | GitHub 커밋 상태 `success`, 공개 주소 `/api/status` 200 |

## 6. 제약과 주의점

### 6-1. 비밀 키 노출 금지
| 대상 | 규칙 | 현재 |
|---|---|---|
| 채팅·문서·코드·커밋 | API 키·비밀번호를 쓰지 않는다. 사용자가 `.env`에 직접 넣는다 | 저장소 `nvapi-` 검색 0건 |
| `.env` | git 제외(`.gitignore`), Vercel 업로드 제외(`.vercelignore`) | 확인됨 |
| Vercel 환경변수 | **최소 권한만**: 읽기 전용 계산 역할·서비스 역할 비밀번호, AI 키(운영만). 가계부 역할·DB 관리자 비밀번호는 올리지 않음. 비밀은 민감 값(재조회 불가) | 확인됨 |
| 브라우저에 나가는 값 | 공개 가능한 것만: Supabase URL, 공개(anon) 키. 서비스 키·비밀번호는 서버 안에서만 | `/api/status` 응답에 비밀 없음(테스트) |
| 로그 | 값은 가려서 출력(스크립트가 `***`로 치환), Vercel 런타임 로그는 Hobby에서 **1시간만 보관** | 〃 |

### 6-2. 속도 예산
| 항목 | 목표 | 현재(측정) |
|---|---|---|
| 상태 조회 | < 0.5초 | 0.29초 |
| 계산식 실행(웜) | < 2초 | 약 1.0~1.3초 |
| 첫 요청(콜드 스타트) | < 6초 | 약 4초 |
| 정의 비교 | < 3초 | 약 1.9초 (연결 재사용 전 12초) |
| 라이브 AI | 서버 한도(60초, 호출 대기 50초) 안 | 10~31초(gpt-oss-20b), 44초(nemotron) |
| DB 쿼리 자체 | — | 3~190ms. **느린 것은 연결 열기(1.3초)** 라서 연결 재사용이 핵심 |

### 6-3. 지금 돌아가는 기능을 깨지 않기
- **기본 모드(로컬 SQLite)를 항상 유지**한다: `POC_DESK_BACKEND`가 없으면 지금처럼 동작해야 한다(G1).
- 새 기능은 **환경변수·모드로 켜진다**(공개 규칙·Supabase 모드 방식). 기존 경로의 반환 형태를 바꾸지 않는다.
- **마이그레이션은 추가만**: 기존 열·표를 지우거나 바꾸지 않는다(필요하면 새 마이그레이션으로 되돌린다, 0006→0007처럼).
- **동결 자료는 읽기만**: `experiment/`의 기록·질문·사전·골드(G5).
- 배포 전 로컬 G1~G7, 배포 후 G8.

### 6-4. 외부 서비스 한도 (공식 문서 직접 확인, 2026-10-20)
| 서비스 | 항목 | 값 | 확인 |
|---|---|---|---|
| Vercel Hobby | 함수 최대 시간 | **300초**(기본·최대, Fluid compute) — 우리 설정은 60초 | ✅ [한도](https://vercel.com/docs/functions/limitations) |
| 〃 | 메모리 | 2GB / 1 vCPU | ✅ |
| 〃 | Python 번들 크기 | 500MB(압축 해제 기준) | ✅ [Python](https://vercel.com/docs/functions/runtimes/python) |
| 〃 | 요청·응답 본문 | 4.5MB | ✅ |
| 〃 | 파일 기술자 | 1,024개(동시 실행 공유) | ✅ (DB 연결도 포함 → 연결을 쌓지 않는다) |
| 〃 | 배포 횟수 | 하루 100회, 시간당 100회, 동시 배포 1개 | ✅ [한도](https://vercel.com/docs/limits) |
| 〃 | 환경변수 총 크기 | 64KB | ✅ |
| 〃 | 런타임 로그 보관 | **1시간** | ✅ |
| 〃 | 용도 | 비상업·개인 | ◐ 보조 자료([Hobby](https://vercel.com/docs/plans/hobby)를 직접 열어 확인하지 못함) |
| 〃 | 월 사용량(대역폭 100GB, 호출 약 100만, CPU 4시간) | 값 인용 | ◐ 검색 결과 인용, 공식 페이지에서 직접 확인 못함 |
| Supabase Free | 프로젝트 수 | **2개**(일시정지 프로젝트는 제외). 현재 2개(기존 1 + realdefinition) → 더 만들 수 없음 | ✅ [청구 문서](https://supabase.com/docs/guides/platform/billing-on-supabase) |
| 〃 | DB 크기 / 송신 / 저장소 / 월 활성 사용자 | 500MB / 5GB / 1GB / 5만 | ✅ |
| 〃 | 일시정지 기준 | 공식 문서에서 **확인 못함** `[미확인]`(보조 자료는 7일 비활성) | ✗ |
| 〃 | 유출 비밀번호 차단 | **Pro 플랜 이상**만 | ✅ [문서](https://supabase.com/docs/guides/auth/password-security) |
| 〃 | 풀러 트랜잭션 모드 | 프리페어드 스테이트먼트·세션 상태(`SET`) 미지원 | ✅ [접속 문서](https://supabase.com/docs/guides/database/connecting-to-postgres) |
| 〃 | 직접 접속(5432 `db.…`) | IPv6만(IPv4는 유료 애드온) → **풀러를 쓴다** | ✅ |

## 7. 알려진 위험
| # | 위험 | 근거 | 영향 | 대응(제안) |
|---|---|---|---|---|
| R1 | **Supabase 옛 키(`anon`·`service_role`) 폐기 예정(2026년 말까지)** | [공식](https://supabase.com/docs/guides/getting-started/migrating-to-new-api-keys) | 우리 코드·QA 화면·`make_config.py`가 `anon` 키를 쓴다. 새 publishable 키는 JWT가 아니라 `Authorization: Bearer`로 쓸 수 없음 | 폐기 전에 새 키로 이전(별도 작업). 서버의 로그인 확인은 Auth 호출 방식이라 서명 키 교체에 영향받지 않음(D-037의 이점) |
| R2 | 트랜잭션 모드가 pg8000에서 동작하지만 **공식 보장은 아님** | 공식: 프리페어드 미지원. 우리는 시험으로 정상 확인 | 업그레이드 때 깨질 수 있음 | 연결 시험(G4)을 업그레이드 때마다 실행, 실패 시 5432로 되돌리는 환경변수 한 줄 |
| R3 | `/api` 폴더 함수(`handler` 클래스)는 공식 문서에서 "기존 프로젝트용" | [Python 런타임](https://vercel.com/docs/functions/runtimes/python) | 장기적으로 신규 형식(`app` 변수)으로 옮겨야 할 수 있음 | 표준 라이브러리 `wsgiref`로 감싸는 방안을 별도 작업으로 검토 |
| R4 | 무료 프로젝트가 2개로 가득 참, 일시정지 기준 `[미확인]` | 6-4 | 오래 쓰지 않으면 DB가 멈춰 공개 데모가 오류 | 데모 전에 대시보드에서 상태 확인, 기존 프로젝트 정리 여부는 사용자 결정 |
| R5 | 로컬 Python 3.11 ≠ 배포 3.12 | 6-4 | 로컬에서 되고 배포에서 안 되는(또는 반대) 가능성 | `.python-version`으로 배포 버전을 3.12로 명시, 로컬 테스트를 3.12로도 돌려 보기 `[미실행]` |
| R6 | 속도 제한이 인스턴스별 | 16 문서 | 완전한 방어 아님 | Vercel 방화벽 규칙 검토 |
| R7 | 라이브 AI 호출이 느린 날 시간 초과 | 실측 10~44초 | 실패 시 오류 기록 | 한도(300초)까지 늘리거나 빠른 모델 유지. 현재 보수적으로 60/50초 |

## 열린 질문
- R1(키 이전)을 언제 할지. 폐기 시점은 공식 문서상 "2026년 말까지"라 가깝다.
- 함수 최대 시간을 60초에서 늘릴지(공식 한도 300초).
- R3: `/api` 형식을 유지할지 `app` 변수 형식으로 옮길지.

## 변경 이력
- 2026-10-20 초안 작성(INT-007): 설치 버전은 직접 읽고, 외부 서비스 한도는 공식 문서로 확인
