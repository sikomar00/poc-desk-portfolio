-- erd.md 기준 스키마·테이블. 데이터는 넣지 않는다.
-- app.profiles가 personal.transactions보다 먼저 있어야 해서 app을 먼저 만든다.

create schema if not exists demo;
create schema if not exists personal;
create schema if not exists app;

-- ===== app =====
create table app.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  display_name text not null,
  created_at timestamptz not null default now()
);

create table app.kpi_definitions (
  definition_id bigint generated always as identity primary key,
  dataset text not null check (dataset in ('demo', 'personal')),
  metric_key text not null,
  version int not null,
  name text not null,
  aliases text[] not null default '{}',
  formula text not null,
  include_exclude text not null,
  period_basis text not null,
  unit text not null,
  source text not null check (source in ('builtin', 'custom')),
  has_reference boolean not null,
  created_at timestamptz not null default now(),
  unique (dataset, metric_key, version)
);

create table app.ledger (
  definition_id bigint not null references app.kpi_definitions (definition_id),
  period_month text not null check (period_month ~ '^\d{4}-\d{2}$'),
  value numeric not null,
  primary key (definition_id, period_month)
);

create table app.query_runs (
  run_id text primary key,
  user_id uuid references app.profiles (user_id), -- NULL = 비로그인 공개 데모 실행
  ts timestamptz not null,
  dataset text not null check (dataset in ('demo', 'personal')),
  definition_id bigint references app.kpi_definitions (definition_id),
  question text not null,
  months text[] not null,
  mode text not null check (mode in ('reference', 'ai')),
  model text,
  sql text,
  value numeric,
  reference_value numeric,
  overall text not null,
  status text not null,
  error text,
  elapsed_ms int,
  checks jsonb not null default '[]'
);

-- ===== demo (SQLite 합성 DB와 같은 이름·의미) =====
create table demo.customers (
  customer_id int primary key,
  name text not null,
  joined_at date not null
);

create table demo.products (
  product_id int primary key,
  name text not null,
  category text not null,
  unit_price_ex_vat int not null
);

create table demo.orders (
  order_id int primary key,
  customer_id int not null references demo.customers (customer_id),
  ordered_at timestamp not null,
  paid_at timestamp, -- NULL = 결제 전
  status text not null check (status in ('PAID', 'CANCELLED', 'PENDING')),
  items_ex_vat int not null,
  vat_amount int not null,
  shipping_fee int not null,
  total_amount int not null
);

create table demo.order_items (
  item_id int primary key,
  order_id int not null references demo.orders (order_id),
  product_id int not null references demo.products (product_id),
  quantity int not null,
  unit_price_ex_vat int not null
);

create table demo.refunds (
  refund_id int primary key,
  order_id int not null references demo.orders (order_id),
  refunded_at timestamp not null,
  refund_type text not null check (refund_type in ('FULL', 'PARTIAL')),
  refund_amount int not null -- 부가세 포함
);

-- ===== personal (가계부) =====
create table personal.import_batches (
  batch_id bigint generated always as identity primary key,
  source_file_sha256 text not null unique,
  row_count int not null,
  loaded_at timestamptz not null default now()
);

create table personal.categories (
  category_id int generated always as identity primary key,
  parent_id int references personal.categories (category_id),
  name text not null,
  unique nulls not distinct (parent_id, name)
);

create table personal.payment_methods (
  payment_method_id int generated always as identity primary key,
  name text not null unique
);

create table personal.transactions (
  tx_id bigint generated always as identity primary key,
  owner_id uuid not null references app.profiles (user_id),
  batch_id bigint not null references personal.import_batches (batch_id),
  source_row_no int not null,
  occurred_at timestamptz not null,
  tx_type text not null, -- 원본 값(지출/이체/수입) 그대로
  category_id int not null references personal.categories (category_id),
  payment_method_id int not null references personal.payment_methods (payment_method_id),
  merchant text not null,
  amount bigint not null, -- 원 단위, 지출은 음수
  dup_group int, -- NULL = 중복 의심 없음
  unique (batch_id, source_row_no)
);
