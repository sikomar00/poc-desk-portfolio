-- 트랜잭션 모드 접속(포트 6543)은 연결을 나눠 쓰므로 세션마다 SET search_path 를 보낼 수 없다.
-- 읽기 전용 계산 역할의 기본 검색 경로를 demo 로 고정해, 테이블 이름을 스키마 없이 써도 되게 한다(세션 모드에서도 동일).
alter role poc_reader_demo set search_path = demo;
