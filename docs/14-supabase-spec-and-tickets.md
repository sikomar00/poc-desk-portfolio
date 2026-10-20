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

## 열린 질문
- T5의 가명 처리 규칙(가맹점 원문을 어디까지 가릴지).
- 서버를 외부에 배포할 계획이 생기면 S1 재검토.

## 변경 이력
- 2026-10-20 S1·S2를 D-035로 대체 표시(로그인), DDL 갱신은 T2로 이월
- 2026-10-20 초안 작성 (스펙 결정 S1~S5, DDL 초안, 티켓 T1~T8)
