---
id: erd
title: realDefinition ERD (로그인 포함, Supabase 이전 목표 구조)
status: draft
updated: 2026-10-20
depends_on: [13-supabase-db-plan, 14-supabase-spec-and-tickets]
---

# realDefinition ERD

## 요약
[13 문서](docs/13-supabase-db-plan.md)의 ERD에 **로그인(Supabase Auth)** 을 더한 전체 구조입니다. 이전·적용은 하지 않았고 설계만 있습니다. 이 파일이 ERD의 기준(최신)이며, 13 문서의 ERD는 로그인 도입 전 버전입니다.

## 로그인 결정 (인터뷰 Q1~Q6)
| # | 질문 | 결정 |
|---|---|---|
| Q1 | 방식 | Supabase Auth(이메일·비밀번호). 서버는 세션(JWT) 유효성만 확인하고, 가계부는 계속 서버가 직접 접속해 읽는다(ADR 0002 유지) |
| Q2 | 가입 범위 | 가입 화면 없음. 본인 계정 1개를 직접 만든다. 공개 데모(합성 데이터)는 비로그인 |
| Q3 | 로그 | `query_runs`에만 `user_id` 추가(비로그인 데모 실행은 NULL). 로그인 성공·실패 기록 테이블은 만들지 않음 |
| Q4 | 프로필·소유 | `app.profiles(user_id → auth.users)`를 만들고 `personal.transactions.owner_id`가 이를 가리킨다. role 컬럼 없음 |
| Q5 | 로그인 필요 기능 | 가계부 계산·사용자 정의 지표 저장·실행 기록 조회는 로그인. 합성 데이터 계산은 비로그인 가능, 비로그인은 DB에 쓰는 경로 없음 |
| Q6 | 세션 | Supabase 기본 세션(JWT 1시간, 자동 갱신), 로그아웃 버튼 있음, 비밀번호 재설정 화면 없음(대시보드에서 직접) |

> 📘 **개념 학습 — Supabase Auth와 auth.users**
> - **Supabase Auth**: 이메일·비밀번호 확인, 비밀번호 암호화 저장, 로그인 세션 발급을 대신해 주는 서비스다.
> - **`auth.users`**: Auth가 관리하는 계정 테이블. 우리가 구조를 바꾸지 못하므로 우리 테이블은 이 테이블을 **직접 가리키지 않고** `profiles`를 사이에 둔다.
> - **JWT**: 로그인 성공 시 받는 "신분증 문자열". 서버는 서명이 유효한지, 만료되지 않았는지만 확인하면 누구인지 알 수 있다.
> - **`on delete cascade`**: 계정이 지워지면 프로필 행도 같이 지워지게 하는 설정.

## ERD
```mermaid
erDiagram
    AUTH_USERS ||--o| APP_PROFILES : "has profile"
    APP_PROFILES ||--o{ PERSONAL_TRANSACTIONS : owns
    APP_PROFILES ||--o{ APP_QUERY_RUNS : runs

    DEMO_CUSTOMERS ||--o{ DEMO_ORDERS : places
    DEMO_ORDERS ||--|{ DEMO_ORDER_ITEMS : contains
    DEMO_PRODUCTS ||--o{ DEMO_ORDER_ITEMS : "sold as"
    DEMO_ORDERS ||--o{ DEMO_REFUNDS : "refunded by"

    PERSONAL_IMPORT_BATCHES ||--o{ PERSONAL_TRANSACTIONS : loaded
    PERSONAL_CATEGORIES ||--o{ PERSONAL_CATEGORIES : "parent of"
    PERSONAL_CATEGORIES ||--o{ PERSONAL_TRANSACTIONS : classifies
    PERSONAL_PAYMENT_METHODS ||--o{ PERSONAL_TRANSACTIONS : "paid with"

    APP_KPI_DEFINITIONS ||--o{ APP_LEDGER : "checked against"
    APP_KPI_DEFINITIONS ||--o{ APP_QUERY_RUNS : "used by"

    AUTH_USERS {
        uuid id PK "managed by Supabase"
        text email
    }
    APP_PROFILES {
        uuid user_id PK "FK to auth users"
        text display_name
        timestamptz created_at
    }

    DEMO_CUSTOMERS {
        int customer_id PK
        text name
        date joined_at
    }
    DEMO_PRODUCTS {
        int product_id PK
        text name
        text category
        int unit_price_ex_vat
    }
    DEMO_ORDERS {
        int order_id PK
        int customer_id FK
        timestamp ordered_at
        timestamp paid_at "NULL = not paid"
        text status "PAID CANCELLED PENDING"
        int items_ex_vat
        int vat_amount
        int shipping_fee
        int total_amount
    }
    DEMO_ORDER_ITEMS {
        int item_id PK
        int order_id FK
        int product_id FK
        int quantity
        int unit_price_ex_vat
    }
    DEMO_REFUNDS {
        int refund_id PK
        int order_id FK
        timestamp refunded_at
        text refund_type "FULL PARTIAL"
        int refund_amount "incl VAT"
    }

    PERSONAL_IMPORT_BATCHES {
        bigint batch_id PK
        text source_file_sha256 UK
        int row_count
        timestamptz loaded_at
    }
    PERSONAL_CATEGORIES {
        int category_id PK
        int parent_id FK "NULL = top level"
        text name
    }
    PERSONAL_PAYMENT_METHODS {
        int payment_method_id PK
        text name UK
    }
    PERSONAL_TRANSACTIONS {
        bigint tx_id PK
        uuid owner_id FK "profiles user_id, RLS key"
        bigint batch_id FK
        int source_row_no
        timestamptz occurred_at
        text tx_type "original values"
        int category_id FK
        int payment_method_id FK
        text merchant "raw text"
        bigint amount "KRW signed"
        int dup_group "NULL = none"
    }

    APP_KPI_DEFINITIONS {
        bigint definition_id PK
        text dataset "demo personal"
        text metric_key
        int version
        text name
        text_array aliases
        text formula
        text include_exclude
        text period_basis
        text unit
        text source "builtin custom"
        boolean has_reference
        timestamptz created_at
    }
    APP_LEDGER {
        bigint definition_id FK
        text period_month "YYYY-MM"
        numeric value
    }
    APP_QUERY_RUNS {
        text run_id PK
        uuid user_id FK "NULL = public demo run"
        timestamptz ts
        text dataset
        bigint definition_id FK
        text question
        text_array months
        text mode "reference ai"
        text model
        text sql
        numeric value
        numeric reference_value
        text overall
        text status
        text error
        int elapsed_ms
        jsonb checks
    }
```

**읽는 법:** `AUTH_USERS`는 Supabase가 관리하는 테이블이라 우리 DDL로 만들지 않습니다. 13 문서와 달라진 곳은 **`APP_PROFILES` 추가, `owner_id`가 `profiles`를 가리킴, `query_runs.user_id` 추가** 세 가지입니다.

## 로그인 흐름
```mermaid
sequenceDiagram
    participant U as 브라우저
    participant S as realDefinition 서버
    participant A as Supabase Auth
    participant D as Postgres
    U->>S: 이메일·비밀번호
    S->>A: 로그인 요청
    A-->>S: 세션(JWT)
    S->>S: JWT 서명·만료 확인, profiles에 user_id 있는지 확인
    S-->>U: 로그인 완료(세션 쿠키)
    U->>S: 가계부 지표 질문
    S->>S: 로그인 상태 확인 (비로그인이면 가계부 거절)
    S->>D: poc_reader_personal로 SELECT
    D-->>S: 숫자 하나
    S->>D: app_service로 query_runs 기록 (user_id 포함)
    S-->>U: 결과
```

## 로그인에 따른 권한 변화 (14 문서 대비)
| 항목 | 이전(14 문서 S1) | 이번 |
|---|---|---|
| 본인 확인 | 서버 환경변수 토큰 | Supabase Auth 세션 + `profiles` 행 존재 |
| `owner_id` 출처 | 환경변수 고정 UUID | 로그인한 `profiles.user_id` |
| 가계부 RLS | `owner_id = <고정 UUID>` | `owner_id`가 `profiles`에 있는 값(서버가 접속 시 검증), 보조 방어선 |
| 사용자 정의 지표 저장 | 제한 없음 | 로그인 필요 |

## 열린 질문
- 서버가 세션(JWT)을 검증하는 구현 방법(Supabase 공개키로 직접 검증 vs Auth API 호출)은 구현 스펙에서 결정.
- 본인 계정에 이중 인증(MFA)을 켤지(이번 범위 밖).
- 사용자 정의 지표(`kpi_definitions`)에 작성자를 남길지(지금은 남기지 않음).

## 변경 이력
- 2026-10-20 초안 작성 (13 문서 ERD에 로그인 반영: profiles, owner_id FK, query_runs.user_id)
