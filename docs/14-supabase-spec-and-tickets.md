---
id: 14-supabase-spec-and-tickets
title: Supabase 구현 스펙과 작업 티켓 (DDL 초안·권한·테스트 기준)
status: draft
updated: 2026-10-20
depends_on: [13-supabase-db-plan]
---

# Supabase 구현 스펙과 작업 티켓

## 요약
[13 문서](13-supabase-db-plan.md)의 ERD를 **만들 수 있는 수준**으로 내린 스펙입니다. DDL 초안, 역할·권한, "됐다"를 판정하는 테스트 기준, 순서가 있는 작업 티켓 8개를 담았습니다. **아직 아무것도 실행하지 않았습니다.** DDL은 Postgres에서 실행해 본 적이 없는 초안이라, 티켓 T2에서 처음 실행하면서 고칩니다.

## 1. 스펙 결정 (13 문서의 열린 질문을 닫음)
| # | 질문 | 결정 | 이유 |
|---|---|---|---|
| S1 | 본인 확인 방식 | ~~서버 환경변수 토큰~~ → **D-035로 대체**: Supabase Auth 세션 + `app.profiles` 행 존재. 상세는 [erd.md](../erd.md) | 사용자 구분과 실행 기록이 필요해졌다 |
| S2 | `owner_id` 값 | ~~고정 UUID~~ → **D-035로 대체**: 로그인한 본인의 `profiles.user_id`를 적재 때 넣는다 | RLS를 보조 방어선으로 켜 두기 위한 값 |
| S3 | 리포트 파일 | 이번에는 DB로 옮기지 않는다 | 범위 밖(13 문서 1절) |
| S4 | 대사 허용 오차 | 0 유지(D-024) | 이전으로 기준이 느슨해지면 안 된다 |
| S5 | 앱 코드 전환 방식 | 접근 계층을 한 곳(`app/db.py`)으로 모으고 SQLite/Postgres를 환경변수로 고른다. SQLite는 기본값으로 남긴다 | 이전 후에도 로컬 테스트 31개가 계속 돌아야 한다 |

> **로그인 반영:** DDL에는 `app.profiles`(user_id → `auth.users`, on delete cascade)가 추가되고, `personal.transactions.owner_id`와 `app.query_runs.user_id`가 이를 가리킵니다. `profiles`를 먼저 만들어야 하므로 실행 순서는 `app` → `personal`로 바꿉니다. 아래 2절 DDL 본문은 로그인 도입 전 초안이라 이 변경이 아직 반영되어 있지 않습니다(T2에서 [erd.md](../erd.md) 기준으로 맞춥니다).

## 2. DDL 초안
```sql
-- 스키마
create schema demo;
create schema personal;
create schema app;

-- demo: SQLite와 같은 이름·의미 (값은 변경 없음)
create table demo.customers (
  customer_id int primary key, name text not null, joined_at date not null);
create table demo.products (
  product_id int primary key, name text not null, category text not null,
  unit_price_ex_vat int not null);
create table demo.orders (
  order_id int primary key,
  customer_id int not null references demo.customers,
  ordered_at timestamp not null, paid_at timestamp,
  status text not null check (status in ('PAID','CANCELLED','PENDING')),
  items_ex_vat int not null, vat_amount int not null,
  shipping_fee int not null, total_amount int not null);
create table demo.order_items (
  item_id int primary key,
  order_id int not null references demo.orders,
  product_id int not null references demo.products,
  quantity int not null, unit_price_ex_vat int not null);
create table demo.refunds (
  refund_id int primary key,
  order_id int not null references demo.orders,
  refunded_at timestamp not null,
  refund_type text not null check (refund_type in ('FULL','PARTIAL')),
  refund_amount int not null);

-- personal
create table personal.import_batches (
  batch_id bigint generated always as identity primary key,
  source_file_sha256 text not null unique,
  row_count int not null, loaded_at timestamptz not null default now());
create table personal.categories (
  category_id int generated always as identity primary key,
  parent_id int references personal.categories,
  name text not null, unique (parent_id, name));
create table personal.payment_methods (
  payment_method_id int generated always as identity primary key,
  name text not null unique);
create table personal.transactions (
  tx_id bigint generated always as identity primary key,
  owner_id uuid not null,
  batch_id bigint not null references personal.import_batches,
  source_row_no int not null,
  occurred_at timestamptz not null,
  tx_type text not null,              -- 원본 값(지출/이체/수입) 그대로
  category_id int not null references personal.categories,
  payment_method_id int not null references personal.payment_methods,
  merchant text not null,
  amount bigint not null,             -- 원 단위, 지출은 음수
  dup_group int,                      -- NULL = 중복 의심 없음
  unique (batch_id, source_row_no));
alter table personal.transactions enable row level security;
create policy owner_only on personal.transactions
  using (owner_id = '<POC_DESK_OWNER_ID>'::uuid);   -- 실제 값은 환경변수로 주입

-- app
create table app.kpi_definitions (
  definition_id bigint generated always as identity primary key,
  dataset text not null check (dataset in ('demo','personal')),
  metric_key text not null, version int not null,
  name text not null, aliases text[] not null default '{}',
  formula text not null, include_exclude text not null,
  period_basis text not null, unit text not null,
  source text not null check (source in ('builtin','custom')),
  has_reference boolean not null,
  created_at timestamptz not null default now(),
  unique (dataset, metric_key, version));
create table app.ledger (
  definition_id bigint not null references app.kpi_definitions,
  period_month text not null check (period_month ~ '^\d{4}-\d{2}$'),
  value numeric not null, primary key (definition_id, period_month));
create table app.query_runs (
  run_id text primary key, ts timestamptz not null,
  dataset text not null,
  definition_id bigint references app.kpi_definitions,
  question text not null, months text[] not null,
  mode text not null check (mode in ('reference','ai')),
  model text, sql text, value numeric, reference_value numeric,
  overall text not null, status text not null, error text,
  elapsed_ms int, checks jsonb not null default '[]');
```

**스펙의 알려진 틈 (티켓에서 확정):**
- 가계부 `category_id`를 `not null`로 둔 것은 현재 데이터에 널이 없다는 조회 결과에 기반합니다. 적재 때 "미분류"가 나오면 규칙을 정합니다(T5).
- `dup_group` 번호 부여 규칙(정렬 기준)은 T5에서 정하고 테스트로 고정합니다.
- 정의와 기록은 "추가만"(UPDATE/DELETE 권한 없음)이지만, 사용자 정의 지표를 수정하면 새 `version` 행으로 쌓는 구조라 지금 `custom_kpis.json`의 `history`와 대응됩니다.

## 3. 역할·권한 (실행 스펙)
| 역할 | 속성 | 권한 |
|---|---|---|
| `poc_reader_demo` | LOGIN, 일반 | `demo`: USAGE + SELECT. 다른 스키마는 권한 없음 |
| `poc_reader_personal` | LOGIN, 일반 | `personal`: USAGE + SELECT. 다른 스키마는 권한 없음 |
| `app_service` | LOGIN, 일반 | `app`: USAGE + SELECT + INSERT (정의·기록·원장). UPDATE/DELETE 없음 |
| 공통 | — | 세 역할 모두 `default_transaction_read_only`는 `poc_reader_*`에만, `statement_timeout = 5s` |
| `anon`(Supabase 기본) | API | `demo`만 SELECT. API 노출 스키마 목록에서 `personal`·`app` 제거 |

모든 역할은 `public` 스키마의 기본 권한을 회수합니다(`revoke all on schema public from public`). 새로 만든 테이블에 기본 권한이 자동으로 붙지 않게 `alter default privileges`도 명시합니다.

## 4. 판정 기준 (테스트)
| # | 확인 | 합격 조건 |
|---|---|---|
| A1 | `poc_reader_demo`로 `app.ledger` 조회 | 권한 오류 |
| A2 | `poc_reader_demo`로 `personal.transactions` 조회 | 권한 오류 |
| A3 | `poc_reader_personal`로 INSERT/UPDATE/DELETE | 전부 거부 |
| A4 | anon 키로 REST를 호출해 `personal`, `app` 접근 | 모두 거부 또는 없는 리소스 |
| A5 | `poc_reader_*`로 `select pg_sleep(10)` | 5초 부근에서 중단 |
| A6 | `app_service`로 `app.query_runs` UPDATE/DELETE | 거부 |
| A7 | RLS: `owner_id`가 다른 행을 넣고 `personal` 읽기 | 보이지 않음 |
| D1 | 이전 후 행 수 | 합성 15/8/500/1,017/52, 가계부 1,770 |
| D2 | 합성 월별 합계·원장 | SQLite와 차이 0 |
| D3 | 가계부 월별 지출·총유출 | SQLite 결과와 차이 0 |
| D4 | 같은 이전 스크립트 재실행 | 행 수 불변 |
| D5 | 앱 기존 테스트 | `app/test_app.py` 31개, `experiment/test_experiment.py` 32개 통과 |

**A 계열은 데이터를 넣기 전에** 빈 테이블로 먼저 통과시킵니다. 권한 실수가 있어도 노출될 실제 데이터가 아직 없는 상태에서 잡기 위해서입니다.

## 5. 작업 티켓
| 티켓 | 내용 | 선행 | 완료 조건 | 데이터 사용 |
|---|---|---|---|---|
| T1 | Supabase 프로젝트 생성, 무료 플랜 제약(일시정지·백업·용량) 확인해 13 문서 R2 갱신 | — | 제약 문서화, 비밀은 `.env`에만 | 없음 |
| T2 | 스키마·테이블 DDL 적용(2절), 실행하며 DDL 수정 | T1 | 빈 테이블 생성, 2절 DDL 수정 반영 | 없음 |
| T3 | 역할·권한·RLS·API 노출 설정(3절) | T2 | A1~A7 통과 | 없음 |
| T4 | 이전 스크립트 골격(재실행 가능, 해시·건수 기록) + `demo` 적재 | T3 | D1·D2·D4 (합성) | 합성만 |
| T5 | 가계부 CSV 변환 규칙 확정(화폐 KRW 검사, 가명 처리, `dup_group` 규칙, 미분류) | T3 | 규칙이 테스트로 고정됨 | 가계부 로컬만 |
| T6 | 가계부 적재 | T4, T5 | D1·D3·D4 (가계부) | 가계부(본인 계정) |
| T7 | `app.kpi_definitions`·`app.ledger` 이전(내장 5개, 사용자 정의, 원장, 가계부 원장) | T4, T6 | 정의·원장 행 수 일치, 대사 0 | 둘 다 |
| T8 | 앱 접근 계층 `app/db.py`로 모으고 Postgres 모드 추가, 본인 토큰(S1) | T7 | D5 + Postgres 모드로 대사 통과 | 둘 다 |

- **병렬 가능:** T4와 T5는 서로 의존하지 않습니다.
- **돌이킬 수 없는 지점:** T6(가계부가 클라우드에 올라감). 그 전에 A계열이 모두 통과해야 합니다.
- **되돌리기:** SQLite 파일은 T8이 끝나고 대조가 통과할 때까지 유일한 기준으로 유지합니다(13 문서 6절).

## 진행 기록 (2026-10-20)
T1~T3을 Supabase CLI(`npx supabase` 2.121.0)로 진행했습니다. 데이터는 아직 넣지 않았습니다(T4 이후).

| 티켓 | 상태 | 내용 |
|---|---|---|
| T1 | 완료(일부) | 프로젝트 `realdefinition` 생성(서울 `ap-northeast-2`, 기존 프로젝트와 별개). **무료 플랜 제약(일시정지·백업·용량)은 아직 확인하지 않았다** — 13 문서 R2는 열린 상태 |
| T2 | 완료 | `supabase/migrations/…0001_schemas_and_tables.sql` 적용. 로그인 반영(`app.profiles`, `owner_id`·`user_id` FK)과 2절 DDL의 차이는 이 파일이 기준 |
| T3 | 완료 | `…0002_roles_grants_rls.sql`, `…0003_admin_can_set_roles.sql` 적용. API 노출 스키마는 `public`, `graphql_public`, `demo` |

**권한 테스트 결과** (4절 기준, 전부 원격 DB에서 실행):
| 테스트 | 결과 |
|---|---|
| A1 `poc_reader_demo` → `app.ledger` | 거부(`permission denied for schema app`) |
| A2 `poc_reader_demo` → `personal.transactions` | 거부 |
| A3 `poc_reader_personal` 쓰기 | 거부(읽기 전용 트랜잭션 + 권한 없음) |
| A4 anon 키로 REST | `demo` 읽기만 200, `personal`·`app`은 406(노출 안 됨), `demo` 쓰기는 401 |
| A5 `pg_sleep(10)` | 5초 제한으로 취소 |
| A6 `app_service` UPDATE/DELETE | 거부(SELECT·INSERT만 가능) |
| A7 RLS 소유자 정책 | `app.owner_id` 미지정 0행, 맞는 값 1행, 다른 값 0행 (롤백되는 트랜잭션 안에서 확인, 흔적 없음) |

**알게 된 것:**
- `SET ROLE`로 흉내 내면 역할별 기본 설정(읽기 전용, 5초 제한)이 적용되지 않는다. 그 설정은 **실제로 로그인할 때만** 붙으므로, 역할 비밀번호를 설정해 풀러로 직접 로그인해 확인했다.
- 역할 비밀번호와 DB 비밀번호는 `.env`(git 제외)에만 있고 마이그레이션 파일에는 없다.
- `app.owner_id` RLS는 보조 방어선이다. 서버와 같은 역할로 접속한 쪽이 이 값을 직접 지정할 수 있어서, 막아 주는 것은 "값을 안 지정한 접속은 0행"까지다.
- Auth 설정: 이메일 확인 끔(`mailer_autoconfirm = true`), 비밀번호 최소 8자, 사이트 URL `http://127.0.0.1:8765`. **가입은 닫았다**(아래 참고).

**T4 중 `demo` 적재 (같은 날 완료):** `data/export_demo_sql.py`가 SQLite에서 SQL을 만들고 `supabase db query --linked -f`로 적용했다. D1 행 수(고객 15, 상품 8, 주문 500, 항목 1,017, 환불 52)와 D2 결제월별 주문 수·상품금액·총액이 로컬과 **정확히 일치**했고, 두 번 적용해도 행 수가 그대로였다(D4). anon 키로 REST에서 `demo.orders`를 읽을 수 있는 것도 확인했다. 가계부와 `app.kpi_definitions`·`app.ledger`는 아직 비어 있다(T5~T7).


**본인 계정 등록과 가입 닫기:** 대시보드에서 만든 계정(1개)을 `app.profiles`에 등록했고(`display_name`은 이메일 앞부분), `enable_signup = false`를 적용했다. **정정:** 이때 `auth.email.enable_signup`까지 꺼서 이메일 로그인이 막혀 있었고, QA에서 발견해 고쳤다([15 문서](15-supabase-qa-guide.md) 5절). 최종 확인: 설정 조회에서 `disable_signup: true`, 이메일 확인은 계속 꺼짐(`mailer_autoconfirm: true`), 낯선 이메일로 가입을 시도하면 422 `signup_disabled`로 거절되고 계정 수는 1개 그대로였다. [가정] 앞으로 계정이 더 필요하면 대시보드에서 직접 만든다.

## 앱 연결 기록 (2026-10-20, T7 일부 + T8 합성 데이터 경로)
앱(realDefinition)이 `POC_DESK_BACKEND=supabase` 일 때 Supabase의 합성 데이터로 질문 → 계산 → 정답 대조 → 리포트를 수행한다. 기본값은 SQLite 그대로다(기존 테스트가 같은 조건에서 돈다, D-034).

| 항목 | 내용 |
|---|---|
| 접속 도구 | `pg8000`(순수 파이썬)을 `app/_vendor`에만 설치(`requirements.txt`, git 제외). 앱 코드는 `app/db.py` 한 곳을 거친다 |
| 접속 보안 | Supabase 공식 루트 인증서(`app/certs/prod-ca-2021.crt`)로 서버 신원과 호스트 이름을 검증한다. 모델·계산식 SQL은 `poc_reader_demo`(읽기 전용, 5초 제한, `demo`만), 정의·원장·기록은 `app_service`가 맡는다 |
| 로그인 | 화면이 Supabase Auth로 직접 로그인하고, 서버는 매 요청의 토큰을 Auth에 확인한 뒤 `app.profiles`에 있는 본인 계정인지 본다(30초 캐시). 토큰은 브라우저 메모리에만 둔다 |
| 로그인 규칙(Q5) | 비로그인: 합성 데이터 계산 가능(기록의 `user_id` 비어 있음). 로그인(본인): 실행 기록 조회·리포트·사용자 정의 지표 저장. 기록은 본인 것만 보인다 |
| 시드 | `data/export_app_seed_sql.py`: 내장 지표 5개(버전 2)와 `net_revenue` 월별 원장 3개월을 `app`에 적재(몇 번 실행해도 같다) |
| 마이그레이션 | `0004`: `app.query_runs`에 표시용 열 4개(`metric_key`·`metric_name`·`definition_version`·`unit`) 추가 |

**검증 결과**
- 기준 계산식 25개 조합(지표 5 × 기간 5): Supabase와 SQLite의 값이 **전부 같다**(불일치 0).
- 1분기 매출 62,831,000원이 DB 원장과 정확히 일치(대사 통과). 월별 원장 값도 SQLite와 같다.
- 브라우저에서 질문 → 정의 확인 → 실행 → 결과·대사 통과, 임시 본인 계정으로 로그인 → 실행 → 리포트 → 기록(1건) → 로그아웃 후 기록 차단까지 확인(임시 계정·기록은 삭제).
- 모델 경로 SQL이 `app`·`personal` 조회와 쓰기를 DB에서 거부한다(`permission denied`, `read-only transaction`).
- 테스트: 앱 41개(기존 31 + 신규 10), 실험 32개 모두 통과.

**한계 (솔직하게)**
- **사용자 정의 지표는 아직 파일**(`app/data/custom_kpis.json`)에 저장한다. DB 이전은 T7의 남은 부분이다. 그래서 사용자 정의 지표를 쓴 실행 기록의 `definition_id`는 비어 있다.
- **AI 모드는 Postgres에서 실제 모델로 시험하지 못했다**(API 키 없음). 프롬프트는 PostgreSQL 문법 안내로 바꿨고, 모델이 SQLite 문법(`strftime` 등)을 쓰면 오류로 기록된다.
- 가계부(`personal`)는 아직 앱에 연결하지 않았다(T5·T6 선행).
- 서버가 로그인 토큰 검증마다 Supabase Auth를 한 번 호출한다(30초 캐시). Supabase가 느리면 로그인 요청이 느려진다.

## 추가 점검 기록 (2026-10-20, Supabase 자동 진단 + 안정성)
| 점검 | 결과 | 조치 |
|---|---|---|
| 보안 진단(Advisors) | 경고 1건: 유출된 비밀번호 차단이 꺼져 있음. 공개 노출·RLS 누락 경고는 0건 | 이 설정이 현재 요금제에서 가능한지는 **미확인**(유료 기능일 수 있음) |
| 성능 진단 | 외래키 색인 없음 9건, RLS 정책이 행마다 설정값을 다시 읽는다는 경고 1건 | 마이그레이션 `0005`: 색인 9개 추가와 정책을 `(select …)`로 감쌈. 외래키 경고는 0건이 됨 |
| RLS 경고 잔존 | 정책을 고친 뒤에도 경고가 남음 | **오탐으로 판단**: 실행 계획에 `InitPlan`(질의당 1회 계산)과 색인 사용이 나타남. 진단 도구가 `select current_setting(`이라는 문자열 모양만 보는 것으로 추정. 정책 의미(미지정 0건, 지정 2건, 다른 값 0건)는 다시 확인함 |
| 연결 끊김 | 서버 쪽에서 연결을 끊은 뒤에도 다음 질문 성공 | — |
| 동시 접속 | 20건 동시 실행 모두 성공, 값 동일, 약 2초 | — |
| 오류 문구 | 시간 초과·권한·문법 오류가 내부 사전 모양(파일 이름 포함)으로 화면에 나감 | `db.friendly_error`로 한국어 문구로 변환, 단위 테스트 1개 추가(앱 42개) |

참고: 진단이 새로 알려 준 "사용 안 함 색인 9건"(정보)은 색인을 방금 만들었고 데이터가 적어 아직 쓰이지 않은 것이다. 가계부를 적재한 뒤 다시 확인한다.

## 열린 질문
- T5의 가명 처리 규칙(가맹점 원문을 어디까지 가릴지).
- 서버를 외부에 배포할 계획이 생기면 S1 재검토.

## 변경 이력
- 2026-10-20 추가 점검(진단·안정성)과 0005 마이그레이션 기록
- 2026-10-20 앱 연결(합성 데이터 경로) 기록 추가
- 2026-10-20 가입 닫기 정정(이메일 로그인 복구), QA 가이드 링크
- 2026-10-20 본인 계정 등록·가입 닫기 결과 추가
- 2026-10-20 demo 적재와 대조 결과 추가
- 2026-10-20 T1~T3 진행 기록과 권한 테스트 결과 추가
- 2026-10-20 S1·S2를 D-035로 대체 표시(로그인), DDL 갱신은 T2로 이월
- 2026-10-20 초안 작성 (스펙 결정 S1~S5, DDL 초안, 티켓 T1~T8)
