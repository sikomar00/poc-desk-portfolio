-- 앱 화면·리포트가 실행 기록을 그대로 다시 그릴 수 있게 표시용 열을 추가한다(사용자 정의 지표는 아직 정의 번호가 없다).
alter table app.query_runs
  add column metric_key text,
  add column metric_name text,
  add column definition_version text,
  add column unit text;
