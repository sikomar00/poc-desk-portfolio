-- 역할·권한·RLS. 비밀번호는 이 파일에 넣지 않는다(저장소에 올라가므로). 별도로 설정한다.

-- 읽기 전용 역할 2개 + 앱 서비스 역할 1개 (비밀번호가 없으면 로그인되지 않는다)
create role poc_reader_demo login noinherit;
create role poc_reader_personal login noinherit;
create role app_service login noinherit;

-- 모델이 만든 SQL은 읽기 전용 트랜잭션, 5초 제한 안에서만 실행한다
alter role poc_reader_demo set default_transaction_read_only = on;
alter role poc_reader_demo set statement_timeout = '5s';
alter role poc_reader_personal set default_transaction_read_only = on;
alter role poc_reader_personal set statement_timeout = '5s';
alter role app_service set statement_timeout = '10s';

-- 세 스키마 모두 기본 권한을 모두 회수한 뒤 필요한 것만 준다
revoke all on schema demo, personal, app from public, anon, authenticated;
revoke all on all tables in schema demo, personal, app from public, anon, authenticated;

-- demo: 공개 읽기 (anon = 비로그인, authenticated = 로그인)
grant usage on schema demo to anon, authenticated, poc_reader_demo;
grant select on all tables in schema demo to anon, authenticated, poc_reader_demo;

-- personal: 가계부 읽기 전용 역할만 (API에는 노출하지 않는다)
grant usage on schema personal to poc_reader_personal;
grant select on all tables in schema personal to poc_reader_personal;

-- app: 정의·원장·기록은 앱 서비스 역할만, 추가만 가능(UPDATE/DELETE 없음)
grant usage on schema app to app_service;
grant select, insert on all tables in schema app to app_service;

-- 앞으로 만드는 테이블에 권한이 저절로 붙지 않게 한다
alter default privileges in schema demo revoke all on tables from public, anon, authenticated;
alter default privileges in schema personal revoke all on tables from public, anon, authenticated;
alter default privileges in schema app revoke all on tables from public, anon, authenticated;

-- ===== RLS =====
alter table demo.customers enable row level security;
alter table demo.products enable row level security;
alter table demo.orders enable row level security;
alter table demo.order_items enable row level security;
alter table demo.refunds enable row level security;

create policy demo_public_read on demo.customers for select to anon, authenticated, poc_reader_demo using (true);
create policy demo_public_read on demo.products for select to anon, authenticated, poc_reader_demo using (true);
create policy demo_public_read on demo.orders for select to anon, authenticated, poc_reader_demo using (true);
create policy demo_public_read on demo.order_items for select to anon, authenticated, poc_reader_demo using (true);
create policy demo_public_read on demo.refunds for select to anon, authenticated, poc_reader_demo using (true);

alter table personal.import_batches enable row level security;
alter table personal.categories enable row level security;
alter table personal.payment_methods enable row level security;
alter table personal.transactions enable row level security;

create policy reader_read on personal.import_batches for select to poc_reader_personal using (true);
create policy reader_read on personal.categories for select to poc_reader_personal using (true);
create policy reader_read on personal.payment_methods for select to poc_reader_personal using (true);

-- 가계부 거래: 서버가 본인 확인 뒤 트랜잭션마다 app.owner_id를 지정해야만 행이 보인다(보조 방어선)
create policy owner_only on personal.transactions for select to poc_reader_personal
  using (owner_id = nullif(current_setting('app.owner_id', true), '')::uuid);

alter table app.profiles enable row level security;
alter table app.kpi_definitions enable row level security;
alter table app.ledger enable row level security;
alter table app.query_runs enable row level security;

create policy service_read on app.profiles for select to app_service using (true);
create policy service_insert on app.profiles for insert to app_service with check (true);
create policy service_read on app.kpi_definitions for select to app_service using (true);
create policy service_insert on app.kpi_definitions for insert to app_service with check (true);
create policy service_read on app.ledger for select to app_service using (true);
create policy service_insert on app.ledger for insert to app_service with check (true);
create policy service_read on app.query_runs for select to app_service using (true);
create policy service_insert on app.query_runs for insert to app_service with check (true);
