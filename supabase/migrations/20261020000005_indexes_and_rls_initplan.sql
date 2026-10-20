-- Supabase 자동 진단(성능)이 알려 준 두 가지를 정리한다.
-- 1) 외래키에 색인이 없어 조인·삭제 확인이 느려질 수 있다. 2) RLS 정책이 행마다 설정값을 다시 읽는다.

-- 실행 기록: 내 기록을 최신순으로 읽는 질의를 위한 색인과 외래키 색인
create index query_runs_user_ts_idx on app.query_runs (user_id, ts desc);
create index query_runs_definition_idx on app.query_runs (definition_id);

-- 합성 데이터 외래키
create index orders_customer_idx on demo.orders (customer_id);
create index order_items_order_idx on demo.order_items (order_id);
create index order_items_product_idx on demo.order_items (product_id);
create index refunds_order_idx on demo.refunds (order_id);

-- 가계부 외래키 (소유자·날짜 순 조회가 가장 흔하다)
create index transactions_owner_time_idx on personal.transactions (owner_id, occurred_at);
create index transactions_category_idx on personal.transactions (category_id);
create index transactions_payment_method_idx on personal.transactions (payment_method_id);

-- RLS: 설정값을 (select ...)로 감싸 질의당 한 번만 읽게 한다. 의미는 그대로(값을 지정하지 않으면 0행).
drop policy owner_only on personal.transactions;
create policy owner_only on personal.transactions for select to poc_reader_personal
  using (owner_id = (select nullif(current_setting('app.owner_id', true), '')::uuid));
