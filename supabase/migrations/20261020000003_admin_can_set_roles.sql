-- 관리자(postgres)가 새 역할로 전환해 권한을 시험할 수 있게 한다(권한 테스트용).
grant poc_reader_demo, poc_reader_personal, app_service to postgres with set true;
